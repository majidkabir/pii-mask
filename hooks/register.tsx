import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, TurnStepChunk } from 'claude-code'

import type { VaultEntry } from '../types'
import {
  cleanTranscript,
  compareVersions,
  DEFAULT_MASKED_TOOLS,
  findRules,
  hideKnownValues,
  mask,
  MEDIA_BLOCKS,
  MEDIA_NOTE,
  MIN_CLAUDE_CODE,
  nerFinds,
  newMarker,
  parseVaultFile,
  PII_LABELS,
  StreamUnmasker,
  systemNote,
  THRESHOLD,
  toolPolicy,
  UNCHECKED_NOTE,
  unmaskDeep,
  Vault,
  vaultFileFor,
  type NerEntity,
  type NerFind,
  type Rule,
  type VaultFile,
} from './masker'
import { PATTERNS } from './patterns'

/*
 * Where data flows and what this mod does there:
 *
 *   you / tools / files ──► session.append ──► stored row + next model request
 *                            (NER, then mask)    (placeholders only)
 *   model stream ──► turn.step ──► screen        (unmask as it streams)
 *   model tool call ──► tool.call ──► tool runs  (unmask arguments)
 *   system prompt, CLAUDE.md, injected text ──► prompt.compose,
 *                            prompt.context, prompt.attachment (NER, then mask)
 *
 * The values are found by a local GLiNER model behind a small HTTP server on
 * 127.0.0.1 (server/server.py). The mod installs it on first use
 * (server/setup.py) and starts it when no session has; it stops by itself
 * after 30 idle minutes. Until it answers, nothing is sent.
 */

const NAME = 'pii-mask'
const PORT = 8766
const BASE = `http://127.0.0.1:${PORT}`
/** Longest one NER call may take. A hook has 10 s in all. */
const NER_TIMEOUT_MS = 7000
/** How long a hook waits for a model that is starting (loaded from disk, not installed). */
const WAIT_FOR_START_MS = 6000
/** How long the model may take to load before the start counts as failed. */
const START_LIMIT_MS = 180_000
const TOOL_ENVELOPE = new Set(['tool', 'tool_use_id', 'agentId', 'requestMeta'])
const LABELS = Object.keys(PII_LABELS)

const vaultAtom = atom({ plugin: 'pii-mask', key: 'vault' } as const, [] as VaultEntry[])
const markerAtom = atom({ plugin: 'pii-mask', key: 'marker' } as const, null as string | null)
const statusAtom = atom({ plugin: 'pii-mask', key: 'status' } as const, `⏳ pii-mask: checking its model`)
const PANE = 'pii-mask'

/** How the pane names each kind of value. */
const KINDS: Record<string, string> = {
  PERSON: 'People', EMAIL: 'Emails', PHONE: 'Phone numbers', ADDRESS: 'Addresses', IBAN: 'IBANs',
  ACCOUNT: 'Bank accounts', CARD: 'Card numbers', ID_NUMBER: 'ID numbers', PASSPORT: 'Passport numbers',
  BIRTHDATE: 'Dates of birth', IP: 'IP addresses', MAC: 'Device addresses', LOCATION: 'Coordinates',
  SECRET: 'Passwords and keys',
}

// Module state: rebuilt on every (re)load; the vault itself is restored from $.state.
let vault = new Vault(newMarker())
const keepsPlaceholders = toolPolicy(DEFAULT_MASKED_TOOLS)
let mediaDropped = 0
let rowsRemoved = 0
let sessionFile: string | undefined
let transcriptPath: string | undefined
/** Why requests are blocked regardless of the model (an old Claude Code, an unreadable table). */
const blocks = new Map<'load' | 'version' | 'vault', string>()
let loading: Promise<void> | undefined

/* ----------------------------------------------------------------- the model */

type EngineState = 'unknown' | 'installing' | 'starting' | 'ready' | 'failed'
const engine = { state: 'unknown' as EngineState, detail: '', cache: '' }
let engineTask: Promise<void> | undefined
/** Set when context Claude Code computes once was replaced by a note; re-rendered once the model runs. */
let contextRedacted = false
const nerCache = new Map<string, NerFind[]>()
const NER_CACHE_SIZE = 500
const nerStats = { calls: 0, ms: 0, failures: 0 }

function statusLine(): string {
  const reason = blocked()
  if (reason) return `🔒 ${NAME}: BLOCKED`
  switch (engine.state) {
    case 'ready': return `🔒 ${NAME} on`
    case 'installing': return `⏳ ${NAME}: installing its model (${engine.detail || 'first run'})`
    case 'starting': return `⏳ ${NAME}: starting its model`
    case 'failed': return `🔒 ${NAME}: model not running`
    default: return `⏳ ${NAME}: checking its model`
  }
}

function setEngine($: EngineInterface, state: EngineState, detail = engine.detail): void {
  engine.state = state
  engine.detail = detail
  const line = statusLine()
  try {
    $.ui.status(line)
  } catch {
    // No surface to show it on (headless): the state is still reported when a request is stopped.
  }
  void update($, statusAtom, () => line).catch(() => undefined) // redraws the pane
}

/** Why requests are blocked, if they are: a block, or a model that isn't running. */
function blocked(): string | undefined {
  const b = blocks.values().next().value
  if (b) return b
  switch (engine.state) {
    case 'ready': return undefined
    case 'installing':
      return `pii-mask is installing its local model, once (about 1–2 GB; now: ${engine.detail || 'starting'}). ` +
        'Send your message again when the status line shows "🔒 pii-mask on".'
    case 'failed':
      return `pii-mask can't run its local model: ${engine.detail} Fix that, then run /pii-mask retry.`
    default:
      return 'pii-mask is starting its local model. Send your message again in a few seconds.'
  }
}

/** Waits for `p` at most `ms` milliseconds; rejects after that. */
async function withTimeout<T>($: EngineInterface, p: Promise<T>, ms: number): Promise<T> {
  let timer: { cancel: () => void } | undefined
  const late = new Promise<never>((_, reject) => {
    timer = $.clock.after(ms, () => reject(new Error(`no answer within ${ms} ms`)))
  })
  try {
    return await Promise.race([p, late])
  } finally {
    timer?.cancel()
  }
}

async function serverUp($: EngineInterface): Promise<boolean> {
  try {
    const res = await withTimeout($, $.http.fetch(`${BASE}/health`), 1500)
    return res.ok && (JSON.parse(res.text) as { app?: string }).app === NAME
  } catch {
    return false
  }
}

async function cacheDir($: EngineInterface): Promise<string> {
  const xdg = await $.env.get('XDG_CACHE_HOME')
  const home = (await $.env.get('HOME')) || (await $.env.get('USERPROFILE'))
  return `${xdg || `${home}/.cache`}/pii-mask`
}

/** A Python 3.9 or newer on the PATH. */
async function findPython($: EngineInterface): Promise<string> {
  for (const exe of ['python3', 'python']) {
    try {
      const r = await $.process.run([exe, '-c', 'import sys; print(sys.version_info >= (3, 9))'], { timeoutMs: 10_000 })
      if (r.exitCode === 0 && r.stdout.trim() === 'True') return exe
    } catch {
      // not on the PATH
    }
  }
  throw new Error('Python 3.9 or newer is needed and none was found (install it from python.org).')
}

/** Starts the model if it isn't running, once at a time. Never rejects. */
function startEngine($: EngineInterface): Promise<void> {
  engineTask ??= runEngine($)
    .catch(err => setEngine($, 'failed', err instanceof Error ? err.message : String(err)))
    .finally(() => {
      engineTask = undefined
    })
  return engineTask
}

async function runEngine($: EngineInterface): Promise<void> {
  if (await serverUp($)) return engineReady($)
  if ((await $.env.get('OS')) === 'Windows_NT') {
    throw new Error('Windows is not supported yet (the model server runs on macOS and Linux).')
  }
  const dir = await cacheDir($)
  engine.cache = dir
  const fake = (await $.env.get('PII_MASK_FAKE_MODEL')) === '1'
  const python = await findPython($)
  const root = $.plugin.root

  if (!(await $.fs.exists(`${dir}/installed.json`))) {
    setEngine($, 'installing', 'preparing')
    const argv = [python, `${root}/server/setup.py`, '--cache', dir, ...(fake ? ['--fake'] : [])]
    const it = $.process.spawn({ argv })[Symbol.asyncIterator]()
    let lastError = ''
    let buffered = ''
    for (;;) {
      const step = await it.next()
      if (step.done) {
        if (step.value.code !== 0) {
          throw new Error(`${lastError || 'the installation failed.'} The log is ${dir}/setup.log.`)
        }
        break
      }
      buffered += step.value.text
      const lines = buffered.split('\n')
      buffered = lines.pop() ?? ''
      for (const line of lines) {
        if (line.startsWith('STEP ')) setEngine($, 'installing', line.slice(5).trim())
        if (line.startsWith('ERROR ')) lastError = line.slice(6).trim()
      }
    }
  }

  setEngine($, 'starting', '')
  const serverPython = fake ? python : `${dir}/venv/bin/python`
  await $.process.run(
    [serverPython, `${root}/server/server.py`, '--daemon', '--port', String(PORT), '--cache', dir, ...(fake ? ['--fake'] : [])],
    { timeoutMs: 30_000 },
  )
  for (let waited = 0; waited < START_LIMIT_MS; waited += 1000) {
    if (await serverUp($)) return engineReady($)
    await $.clock.sleep(1000)
  }
  throw new Error(`the model server did not start within ${START_LIMIT_MS / 1000} s. Its log is ${dir}/server.log.`)
}

async function engineReady($: EngineInterface): Promise<void> {
  setEngine($, 'ready', '')
  if (contextRedacted) {
    contextRedacted = false
    await $.ui.invalidate('prompt.context')
  }
  await $.ui.invalidate('prompt.section')
}

/**
 * Makes sure the model answers before a hook masks with it: starts it when
 * needed and waits a little for one that is starting. Throws while it is
 * being installed or can't run; the hook's .catch then fails closed.
 */
async function needEngine($: EngineInterface): Promise<void> {
  if (engine.state === 'ready') return
  if (engine.state === 'installing' || engine.state === 'failed') throw new Error(blocked())
  const task = startEngine($)
  const state = (): EngineState => engine.state // read fresh: other tasks change it while this waits
  for (let waited = 0; waited < WAIT_FOR_START_MS; waited += 250) {
    if (['ready', 'installing', 'failed'].includes(state())) break
    await Promise.race([task, $.clock.sleep(250)])
  }
  if (state() !== 'ready') throw new Error(blocked())
}

async function detect($: EngineInterface, texts: readonly string[]): Promise<NerEntity[][]> {
  const res = await withTimeout(
    $,
    $.http.fetch(`${BASE}/detect`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ texts, labels: LABELS, threshold: THRESHOLD }),
    }),
    NER_TIMEOUT_MS,
  )
  if (!res.ok) throw new Error(`the model server answered HTTP ${res.status}`)
  const results = (JSON.parse(res.text) as { results?: unknown }).results
  if (!Array.isArray(results) || results.length !== texts.length || !results.every(Array.isArray)) {
    throw new Error('the model server answered in an unknown format')
  }
  return results as NerEntity[][]
}

/**
 * What the model finds in `texts`, in one request (text it already answered
 * for comes from the cache). A server that stopped (it does after 30 idle
 * minutes) is started again once. Rejects when it can't answer in time.
 */
async function nerFind($: EngineInterface, texts: readonly string[]): Promise<NerFind[]> {
  const todo = [...new Set(texts)].filter(t => t.trim() !== '' && !nerCache.has(t))
  if (todo.length > 0) {
    await needEngine($)
    const started = Date.now()
    nerStats.calls += 1
    let results: NerEntity[][]
    try {
      try {
        results = await detect($, todo)
      } catch (err) {
        if (await serverUp($)) throw err
        setEngine($, 'unknown', '')
        await needEngine($)
        results = await detect($, todo)
      }
    } catch (err) {
      nerStats.failures += 1
      throw err
    } finally {
      nerStats.ms += Date.now() - started
    }
    todo.forEach((t, i) => {
      if (nerCache.size >= NER_CACHE_SIZE) nerCache.delete(nerCache.keys().next().value as string)
      nerCache.set(t, nerFinds(results[i] ?? []))
    })
  }
  return texts.flatMap(t => nerCache.get(t) ?? [])
}

/**
 * The rules for one piece of content: the built-in patterns first, then what
 * the model finds in it, then every value found before.
 */
async function rulesFor($: EngineInterface, texts: readonly string[]): Promise<Rule[]> {
  return [...PATTERNS, ...findRules(await nerFind($, texts), vault)]
}

/* ------------------------------------------------------------------ session */

function ready($: EngineInterface): Promise<void> {
  loading ??= load($).catch(err => {
    blocks.set('load', `loading pii-mask failed (${String(err)}).`)
  })
  return loading
}

async function load($: EngineInterface): Promise<void> {
  const { version } = await $.session.version()
  if (compareVersions(version, MIN_CLAUDE_CODE) < 0) {
    blocks.set('version', `Claude Code ${version} is older than ${MIN_CLAUDE_CODE}, the oldest version pii-mask is tested on. Run \`claude update\`.`)
  }
  // The marker is chosen once per session and kept in $.state, so a hot reload keeps the same placeholders.
  const saved = await read($, markerAtom)
  const marker = saved ?? newMarker()
  if (saved === null) await update($, markerAtom, () => marker)
  vault = new Vault(marker)
  vault.load(await read($, vaultAtom))
  await reserveFromTranscript($)
  void startEngine($)
}

/** Reserves placeholder numbers a resumed conversation already uses. Best effort. */
async function reserveFromTranscript($: EngineInterface): Promise<void> {
  let messages: Awaited<ReturnType<EngineInterface['session']['messages']>>
  try {
    messages = await $.session.messages()
  } catch {
    return
  }
  for (const m of messages) {
    vault.reserve(m.text)
    for (const r of m.toolResults ?? []) vault.reserve(r.text ?? '')
    for (const u of m.toolUses) vault.reserve(JSON.stringify(u))
  }
}

async function persist($: EngineInterface, before: number): Promise<void> {
  if (vault.size === before) return
  await update($, vaultAtom, () => vault.entries())
  await writeVaultFile($)
}

async function writeVaultFile($: EngineInterface): Promise<void> {
  if (sessionFile === undefined || vault.size === 0) return
  const data: VaultFile = { version: 1, marker: vault.marker, entries: vault.entries() }
  await $.fs.write(sessionFile, JSON.stringify(data, null, 1))
}

/**
 * Ties the vault to the session's transcript: on start or resume a table
 * already beside it is adopted, so its placeholders resolve again; /clear
 * starts a new one.
 */
async function attach($: EngineInterface, path: string, source: string): Promise<void> {
  const file = vaultFileFor(path)
  if (file === sessionFile) return
  sessionFile = file
  transcriptPath = path
  if (source === 'clear') {
    vault = new Vault(newMarker())
    await update($, markerAtom, () => vault.marker)
    await update($, vaultAtom, () => [])
  } else if (await $.fs.exists(file)) {
    let saved: VaultFile
    try {
      saved = parseVaultFile(await $.fs.read(file))
    } catch (err) {
      blocks.set('vault', `the placeholder table ${file} cannot be read (${String(err)}). Move it away and restart the session.`)
      return
    }
    const minted = vault.size > 0 && vault.marker !== saved.marker
    vault = new Vault(saved.marker)
    vault.load(saved.entries)
    await update($, markerAtom, () => vault.marker)
    await update($, vaultAtom, () => vault.entries())
    if (minted) {
      await $.ui.invalidate('prompt.context')
      await $.ui.invalidate('prompt.section')
    }
  }
  await reserveFromTranscript($)
  await writeVaultFile($)
}

/* ------------------------------------------------------------------ content */

type Block = { type: string; [field: string]: unknown }

const isMedia = (b: Block) => MEDIA_BLOCKS.has(b.type)

/** The texts in a row's blocks that masking rewrites (what the model is asked about). */
function textsOf(content: readonly Block[]): string[] {
  const out: string[] = []
  for (const b of content) {
    if (b.type === 'text' && typeof b.text === 'string') out.push(b.text)
    if (b.type === 'tool_result') {
      if (typeof b.content === 'string') out.push(b.content)
      else if (Array.isArray(b.content)) out.push(...textsOf(b.content as Block[]))
    }
  }
  return out
}

function maskBlock(block: Block, rules: readonly Rule[]): Block {
  if (block.type === 'text' && typeof block.text === 'string') return { ...block, text: mask(block.text, rules, vault) }
  if (block.type === 'tool_result') {
    const c = block.content
    if (typeof c === 'string') return { ...block, content: mask(c, rules, vault) }
    if (Array.isArray(c)) {
      return {
        ...block,
        content: (c as Block[]).map(inner => {
          if (!isMedia(inner)) return maskBlock(inner, rules)
          mediaDropped += 1
          return { type: 'text', text: MEDIA_NOTE }
        }),
      }
    }
  }
  return block
}

/** Masks a row's blocks; its own media blocks are dropped and a note added to its first text. */
function maskContent(content: readonly Block[], rules: readonly Rule[]): Block[] {
  const kept = content.filter(b => !isMedia(b))
  const dropped = content.length - kept.length
  if (dropped === 0) return content.map(b => maskBlock(b, rules))
  mediaDropped += dropped
  let noted = false
  return kept.map(b => {
    const masked = maskBlock(b, rules)
    if (noted || masked.type !== 'text' || typeof masked.text !== 'string') return masked
    noted = true
    return { ...masked, text: `${masked.text}\n\n${MEDIA_NOTE}` }
  })
}

/** A row's content with every text removed: what is stored when masking it failed. */
function redactContent(content: readonly Block[]): Block[] {
  rowsRemoved += 1
  return content
    .filter(b => !MEDIA_BLOCKS.has(b.type))
    .map(b => (b.type === 'text' ? { ...b, text: UNCHECKED_NOTE } : b.type === 'tool_result' ? { ...b, content: UNCHECKED_NOTE } : b))
}

/* ------------------------------------------------------------- local copies */

const MAX_READ = 4 * 1024 * 1024

async function rewriteFile($: EngineInterface, path: string, clean: (text: string) => string): Promise<void> {
  let original: string
  try {
    original = await $.fs.read(path)
  } catch {
    return
  }
  const text = clean(original)
  if (text === original) return
  if ((await $.fs.read(path)) !== original) return
  await $.fs.write(path, text)
}

async function filesIn($: EngineInterface, dir: string, suffix: string): Promise<string[]> {
  if (!(await $.fs.exists(dir))) return []
  const entries = await $.fs.list(dir)
  return entries.filter(f => f.kind === 'file' && f.name.endsWith(suffix) && f.size <= MAX_READ).map(f => `${dir}/${f.name}`)
}

/**
 * Masks the copies Claude Code keeps on disk without passing them through the
 * mod (prompt queue records, tool results' structured records, full outputs
 * in tool-results/) with every value found during the session.
 */
async function cleanSessionFiles($: EngineInterface): Promise<void> {
  if (transcriptPath === undefined || vault.size === 0) return
  const sessionDir = transcriptPath.replace(/\.jsonl$/, '')
  for (const path of [transcriptPath, ...(await filesIn($, `${sessionDir}/subagents`, '.jsonl'))]) {
    await rewriteFile($, path, t => cleanTranscript(t, vault).text)
  }
  for (const path of await filesIn($, `${sessionDir}/tool-results`, '.txt')) {
    await rewriteFile($, path, t => hideKnownValues(t, vault))
  }
}

/* -------------------------------------------------------------------- hooks */

export const register: Register = on => {
  // Every row the conversation keeps passes here before it is stored and sent.
  // If the model can't check it (not running yet, too slow, an error), the
  // .catch stores it with its text removed: Claude Code would otherwise skip
  // the hook and keep the row as it was, to be sent with every later request.
  on('session.append', async ($, e, next) => {
    await ready($)
    const before = vault.size
    const texts = textsOf(e.message.content)
    const rules = texts.length ? await rulesFor($, texts) : []
    const content = maskContent(e.message.content, rules)
    await persist($, before)
    return next({ ...e, message: { ...e.message, content } })
  }).catch(($, e, next) =>
    next.called ? next(e) : next({ ...e, message: { ...e.message, content: redactContent(e.message.content) } }),
  )

  // The session title: set from the masked prompt, so the raw prompt is never sent to title it.
  on('classic.UserPromptSubmit', async ($, e, next) => {
    await ready($)
    if (sessionFile === undefined && e.agent_id === undefined) await attach($, e.transcript_path, 'startup')
    const before = vault.size
    const result = await next(e)
    if (result.sessionTitle !== undefined) return result
    const title = mask(e.prompt, await rulesFor($, [e.prompt]), vault).replace(/\s+/g, ' ').trim().slice(0, 60)
    await persist($, before)
    return { ...result, sessionTitle: title }
  }).catch(async ($, e, next) => ({ ...(await next(e)), sessionTitle: `${NAME}: title withheld` }))

  // The model's reply: unmask as it streams. While the model isn't running, or
  // the session is blocked, answer locally so nothing is sent.
  on('turn.step', async function* ($, e, next) {
    await ready($)
    if (engine.state !== 'ready' && !blocks.size) {
      try {
        await needEngine($)
      } catch {
        // reported below
      }
    }
    const reason = blocked()
    if (reason !== undefined) {
      const text = `${NAME} stopped this request, so no personal data could leak. ${reason}`
      yield { kind: 'text', index: 0, text }
      return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
    }

    type Open = { kind: 'text' | 'thinking'; index: number; u: StreamUnmasker }
    const open = new Map<string, Open>()
    const drain = (exceptKey?: string): TurnStepChunk[] => {
      const out: TurnStepChunk[] = []
      for (const [key, s] of open) {
        if (key === exceptKey) continue
        open.delete(key)
        const rest = s.u.flush()
        if (rest) out.push({ kind: s.kind, index: s.index, text: rest })
      }
      return out
    }
    for await (const chunk of next(e)) {
      if (chunk.kind === 'text' || chunk.kind === 'thinking') {
        const key = `${chunk.kind}:${chunk.index}`
        yield* drain(key)
        let s = open.get(key)
        if (!s) open.set(key, (s = { kind: chunk.kind, index: chunk.index, u: new StreamUnmasker(vault) }))
        const text = s.u.push(chunk.text)
        if (text) yield { ...chunk, text }
        continue
      }
      yield* drain()
      yield chunk
    }
    yield* drain()
  })

  // The model's tool calls: real values back in the arguments, except for
  // tools that would send them off this machine (web tools, every MCP tool).
  on('tool.call', async ($, e, next) => {
    await ready($)
    return next(keepsPlaceholders(String(e.tool)) ? e : unmaskDeep(e, vault, TOOL_ENVELOPE))
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: `${NAME} could not restore the personal data in this call's arguments; retry it.` },
  )

  // The system prompt: mask the per-session sections and explain the placeholders.
  on('prompt.compose', async ($, e, next) => {
    await ready($)
    const before = vault.size
    const { sections } = await next(e)
    const own = sections.filter(s => s.scope === 'session').map(s => s.text)
    const rules = own.length ? await rulesFor($, own) : []
    const masked = sections.map(s => (s.scope === 'session' ? { ...s, text: mask(s.text, rules, vault) } : s))
    await persist($, before)
    return { sections: [...masked, { id: `${NAME}:note`, text: systemNote(vault), scope: 'session' as const }] }
  }).catch(async ($, e, next) => {
    const { sections } = await next(e)
    return { sections: sections.map(s => (s.scope === 'session' ? { ...s, text: UNCHECKED_NOTE } : s)) }
  })

  // CLAUDE.md and the other blocks of the first message (computed once per conversation).
  on('prompt.context', async ($, e, next) => {
    await ready($)
    const before = vault.size
    const result = await next(e)
    const rules = result.blocks.length ? await rulesFor($, result.blocks.map(b => b.text)) : []
    const blocks = result.blocks.map(b => ({ ...b, text: mask(b.text, rules, vault) }))
    await persist($, before)
    return { ...result, blocks }
  }).catch(async ($, e, next) => {
    contextRedacted = true
    const result = await next(e)
    return { ...result, blocks: result.blocks.map(b => ({ ...b, text: UNCHECKED_NOTE })) }
  })

  // Text the engine injects on its own (mentioned files, reminders).
  on('prompt.attachment', async ($, e, next) => {
    await ready($)
    const before = vault.size
    const result = await next(e)
    if (result.text === null) return result
    const masked = { text: mask(result.text, await rulesFor($, [result.text]), vault) }
    await persist($, before)
    return masked
  }).catch(() => ({ text: null }))

  on('classic.SessionStart', async ($, e, next) => {
    await ready($)
    if (e.agent_id === undefined) await attach($, e.transcript_path, e.source)
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    try {
      await cleanSessionFiles($)
    } catch {
      // Best effort: the rows the model reads are masked either way.
    }
    return next(e)
  })

  on('session.start', async ($, e, next) => {
    await ready($)
    await reserveFromTranscript($)
    await $.command.register({
      name: 'pii-mask',
      description: 'Open the pane listing everything pii-mask has masked; "status" prints it, "retry" restarts the model.',
      argumentHint: '[status|retry]',
    })
    $.ui.status(statusLine())
    return next(e)
  })

  on('command.run', { command: 'pii-mask' }, async ($, e) => {
    await ready($)
    const arg = e.args.trim()
    if (arg === 'retry') {
      if (engine.state !== 'ready') setEngine($, 'unknown', '')
      void startEngine($)
      return { text: 'Starting the local model again. The status line shows when it is ready.' }
    }
    if (arg !== '' && arg !== 'status') {
      return { text: `Unknown option "${arg}". Use /pii-mask to open the pane, /pii-mask status, or /pii-mask retry.` }
    }
    if (arg === '') {
      const opened = await $.ui.open({ id: PANE, title: 'Masked by pii-mask' })
      if (opened.isPlaced) return { text: `Opened the pii-mask pane: ${vault.size} value(s) masked so far. It updates as more are found.` }
    }
    const rows = vault.entries().map(v => `  ${vault.format(v.token)}  →  ${v.value}`)
    return {
      text: [
        `${NAME}: ${blocked() ?? 'active'}`,
        `Model: ${engine.state}${engine.detail ? ` (${engine.detail})` : ''}, GLiNER PII on ${BASE}` +
          (engine.cache ? `, files in ${engine.cache}` : ''),
        `Model calls: ${nerStats.calls} (${nerStats.calls ? Math.round(nerStats.ms / nerStats.calls) : 0} ms on average), failed: ${nerStats.failures}`,
        `Images/PDFs removed: ${mediaDropped} · rows removed because they couldn't be checked: ${rowsRemoved}`,
        `Placeholder table: ${sessionFile ?? 'not yet saved (no transcript known)'}`,
        rows.length ? `Replaced this session (${rows.length}):\n${rows.join('\n')}` : 'Nothing replaced yet.',
      ].join('\n'),
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const entries = await read($, vaultAtom)
    const status = await read($, statusAtom)
    const marker = (await read($, markerAtom)) ?? ''
    const lines = paneLines(entries, marker)
    const room = Math.max(3, (e.viewport?.rows ?? 30) - 5)
    const shown = lines.length > room ? lines.slice(0, room - 1) : lines
    return (
      <Box flexDirection="column">
        <Text bold>{status}</Text>
        <Text dimColor>
          {entries.length === 0
            ? 'Nothing masked yet. Names, emails, phone numbers and other personal data appear here as they are found.'
            : `${entries.length} value${entries.length === 1 ? '' : 's'} masked. Claude sees the placeholder; the real value stays on this machine.`}
        </Text>
        {shown.map(l =>
          l.kind === 'head' ? (
            <Text bold color="claude">{l.text}</Text>
          ) : (
            <Box flexDirection="row" gap={2}>
              <Text color="success" wrap="truncate-end">{l.text}</Text>
              <Text dimColor wrap="truncate-end">{l.sub ?? ''}</Text>
            </Box>
          ),
        )}
        {lines.length > shown.length && <Text dimColor>{`… ${lines.length - shown.length} more. /pii-mask status lists them all.`}</Text>}
      </Box>
    )
  })
}

/**
 * The pane: everything masked this session, grouped by kind, newest last.
 * It reads the vault and the status from $.state, so it redraws by itself
 * whenever a value is masked or the model's state changes. It shows real
 * values; it is drawn on this machine only and never sent to the model.
 */
export function paneLines(entries: readonly VaultEntry[], marker: string): { kind: string; text: string; sub?: string }[] {
  const lines: { kind: string; text: string; sub?: string }[] = []
  const order = Object.keys(KINDS)
  const labels = [...new Set(entries.map(e => e.label))].sort((a, b) => order.indexOf(a) - order.indexOf(b))
  for (const label of labels) {
    const group = entries.filter(e => e.label === label)
    lines.push({ kind: 'head', text: `${KINDS[label] ?? label} (${group.length})` })
    for (const e of group) lines.push({ kind: 'row', text: e.value, sub: `<<${e.token}.${marker}>>` })
  }
  return lines
}
