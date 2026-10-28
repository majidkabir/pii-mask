import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, TurnStepChunk } from 'claude-code'

/** Builds a placeholder at run time, so no literal token sits in this file. */
const ph = (label: string, n: number | string, marker: string) => `<<${label}_${n}.${marker}>>`
/** The session marker in masked text. */
const markerIn = (text: string) => /<<[A-Z][A-Z0-9_]*_\d+(?:-\d+)?\.([a-z0-9]{4})>>/.exec(text)?.[1] ?? ''

const CACHE = '/home/u/.cache/pii-mask'
const TRANSCRIPT = '/home/u/.claude/projects/-proj/s1.jsonl'
const VAULT_FILE = '/home/u/.claude/projects/-proj/s1.pii-mask.json'

type World = {
  /** What the model finds: text → model label. Found wherever the text occurs. */
  finds?: Record<string, string>
  /** The model finds things only in the first request it gets. */
  onlyFirstCall?: boolean
  /** The model server answers. */
  up?: boolean
  /** The model is installed (installed.json exists). */
  installed?: boolean
  /** No Python on the PATH. */
  noPython?: boolean
  /** Each /detect takes this long (on the mocked clock). */
  slowMs?: number
  /** The setup script never finishes (it is still downloading). */
  setupHangs?: boolean
  /** The setup script fails, with this reason and fix. */
  setupFails?: { reason: string; fix?: string }
  sections?: { id: string; text: string; scope: 'shared' | 'session' }[]
  reply?: TurnStepChunk[]
  version?: string
}

const FINDS: Record<string, string> = {
  'Lars Holm': 'person',
  'lars@holm.dk': 'email',
  '+45 12 34 56 78': 'phone number',
}

/** The world beneath the plugin: files, a model server, Python, a model that streams `reply`, tools. */
function world(on: On, w: World = {}) {
  const clock = mock.clock(on)
  const files: Record<string, string> = w.installed === false ? {} : { [`${CACHE}/installed.json`]: '{}' }
  const seen = { requestSent: false, ran: '', writes: {} as Record<string, string>, runs: [] as string[][], spawns: [] as string[][], detects: 0 }
  let up = w.up !== false
  const finds = w.finds ?? FINDS

  mock.env(on, { HOME: '/home/u' })
  on('session.cwd', () => ({ value: '/proj' }))
  on('session.version', () => ({ value: { version: w.version ?? '2.1.302' } }))
  on('session.messages', () => ({ value: [] }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.invalidate', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  // Claude Code's own row above the prompt, beneath pii-mask's panel.
  on('ui.render', { component: 'AbovePrompt' }, () => ({ type: 'Text', props: {}, children: ['core row'] }) as never)
  // Claude Code's transcript rows, drawn from what they are given.
  on('ui.render', { component: 'AssistantMessage' }, ($, e) => ({ type: 'Text', props: {}, children: [e.props.text] }) as never)
  on('ui.render', { component: 'UserMessage' }, ($, e) => ({ type: 'Text', props: {}, children: [e.props.text] }) as never)
  on('ui.render', { component: 'ToolUse' }, ($, e) => ({ type: 'Text', props: {}, children: [JSON.stringify(e.props.input)] }) as never)
  on('fs.read', ($, e) => (files[e.path] === undefined ? { deny: `ENOENT: ${e.path}` } : { value: files[e.path] }))
  on('fs.exists', ($, e) => ({ value: e.path in files }))
  on('fs.write', ($, e) => {
    seen.writes[e.path] = e.text
    files[e.path] = e.text
    return { value: undefined }
  })
  on('fs.list', () => ({ value: [] }) as never)
  on('process.run', ($, e) => {
    seen.runs.push([...e.argv])
    if (e.argv.includes('-c')) {
      return w.noPython ? { deny: 'ENOENT' } : { value: { exitCode: 0, stdout: 'True\n', stderr: '' } as never }
    }
    if (e.argv.some(a => a.endsWith('server/server.py'))) up = true
    return { value: { exitCode: 0, stdout: '', stderr: '' } as never }
  })
  on('process.spawn', async function* ($, e) {
    seen.spawns.push([...e.argv])
    yield { stream: 'stdout' as const, text: 'STEP 1/4 creating a Python environment\nSTEP 2/4 installing PyTorch (the largest download)\nSIZE 412000000\n' }
    if (w.setupFails) {
      yield { stream: 'stdout' as const, text: `${w.setupFails.fix ? `FIX ${w.setupFails.fix}\n` : ''}ERROR ${w.setupFails.reason}\n` }
      return { value: { code: 1, signal: null } } as never // a call on $ is answered with { value }
    }
    if (w.setupHangs) await clock.sleep(10_000_000)
    files[`${CACHE}/installed.json`] = '{}'
    return { value: { code: 0, signal: null } } as never
  })
  const answer = (body: unknown) => ({ value: { status: 200, ok: true, headers: {}, text: JSON.stringify(body) } })
  on('http.fetch', async ($, e) => {
    if (!up) return { deny: 'connect ECONNREFUSED 127.0.0.1:8766' }
    if (e.url.endsWith('/health')) return answer({ app: 'pii-mask', ok: true, model: 'test' })
    seen.detects += 1
    const { texts } = JSON.parse(e.init?.body ?? '{}') as { texts: string[] }
    if (w.slowMs) await clock.sleep(w.slowMs)
    const findNow = !w.onlyFirstCall || seen.detects === 1
    return answer({
      results: texts.map(t =>
        findNow ? Object.entries(finds).filter(([v]) => t.includes(v)).map(([text, label]) => ({ text, label, score: 0.9 })) : [],
      ),
    })
  })
  on('classic.SessionStart', () => ({}))
  on('classic.UserPromptSubmit', () => ({}))
  on('session.end', ($, e) => ({ sessionId: e.sessionId }))
  on('turn.step', async function* () {
    seen.requestSent = true
    for (const c of w.reply ?? []) yield c
    return { turnId: 't', index: 0, answer: '', toolUses: [], stopReason: 'end_turn', usage: null }
  })
  on('tool.call', ($, e) => {
    if (e.tool === 'Bash') seen.ran = e.command
    if (e.tool === 'WebSearch') seen.ran = e.query
    return { result: { stdout: 'ok', stderr: '', interrupted: false } } as never
  })
  on('prompt.compose', () => ({ sections: w.sections ?? [] }))
  on('prompt.context', () => ({ blocks: [] }) as never)
  on('prompt.attachment', () => ({ text: null }) as never)
  return { seen, clock }
}

async function appendPrompt($: Engine, text: string, uuid = 'u1') {
  return $.session.append({
    message: { type: 'user', role: 'user', content: [{ type: 'text', text }] },
    door: 'prompt',
    origin: { kind: 'user' },
    uuid,
  } as never)
}

async function appendResult($: Engine, content: unknown, uuid = 'r1') {
  return $.session.append({
    message: { type: 'user', role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content }] },
    door: 'tool-result',
    origin: { kind: 'tool', tool: 'Read' },
    uuid,
  } as never)
}

async function collect(stream: AsyncIterable<TurnStepChunk>) {
  const chunks: TurnStepChunk[] = []
  for await (const c of stream) chunks.push(c)
  return chunks
}
const textOf = (chunks: TurnStepChunk[]) => chunks.map(c => (c.kind === 'text' ? c.text : '')).join('')
const COMPOSE = { model: 'claude', promptModel: 'claude', surfaces: [], tools: [], outputStyle: null, traits: [] }

describe('masking with the model', () => {
  test('a prompt is stored and sent with placeholders for what the model found', async ($, on) => {
    world(on)
    const stored = await appendPrompt($, 'Write to Lars Holm (lars@holm.dk, +45 12 34 56 78) about the invoice.')
    const text = JSON.stringify(stored.message?.content)
    const m = markerIn(text)
    expect(m).toMatch(/^[a-z0-9]{4}$/)
    expect(text).not.toContain('Lars')
    expect(text).not.toContain('holm.dk')
    expect(text).not.toContain('56 78')
    expect(text).toContain(`Write to ${ph('PERSON', 1, m)} (${ph('EMAIL', 1, m)}, ${ph('PHONE', 1, m)}) about the invoice.`)
  })

  test('a value found once stays masked where the model misses it later, in any case', async ($, on) => {
    world(on, { onlyFirstCall: true })
    await appendPrompt($, 'Ask Lars Holm.')
    const stored = await appendResult($, 'owner: LARS HOLM')
    const text = JSON.stringify(stored.message?.content)
    expect(text).not.toMatch(/lars/i)
    expect(text).toContain(`owner: ${ph('PERSON', '1-2', markerIn(text))}`)
  })

  test('the model\'s mistakes are dropped: code, Claude Code\'s own words, non-emails', async ($, on) => {
    world(on, { finds: { onMatch: 'person', Bash: 'person', 'not-an-email': 'email', 'Anna Berg': 'person' } })
    const stored = await appendPrompt($, 'onMatch runs in Bash; not-an-email; Anna Berg approved.')
    const text = JSON.stringify(stored.message?.content)
    expect(text).toContain('onMatch runs in Bash; not-an-email;')
    expect(text).not.toContain('Anna Berg')
  })

  test('real values come back in a Bash call; the table is saved beside the transcript', async ($, on) => {
    const { seen } = world(on)
    await $.classic.SessionStart({ source: 'startup', transcript_path: TRANSCRIPT } as never)
    const stored = await appendPrompt($, 'Mail lars@holm.dk')
    const m = markerIn(JSON.stringify(stored.message?.content))
    await $.tool.call({ tool: 'Bash', command: `echo ${ph('EMAIL', 1, m)}` } as never)
    expect(seen.ran).toBe('echo lars@holm.dk')
    const saved = JSON.parse(seen.writes[VAULT_FILE] ?? '{}')
    expect(saved.entries).toEqual([{ token: 'EMAIL_1', value: 'lars@holm.dk', label: 'EMAIL' }])
  })

  test('web search keeps the placeholders', async ($, on) => {
    const { seen } = world(on)
    const stored = await appendPrompt($, 'Lars Holm')
    const p = ph('PERSON', 1, markerIn(JSON.stringify(stored.message?.content)))
    await $.tool.call({ tool: 'WebSearch', query: `${p} news` } as never)
    expect(seen.ran).toBe(`${p} news`)
  })

  test('the reply is unmasked as it streams, even with a placeholder split across chunks', async ($, on) => {
    let m = ''
    const reply: TurnStepChunk[] = []
    world(on, { reply })
    const stored = await appendPrompt($, 'Lars Holm')
    m = markerIn(JSON.stringify(stored.message?.content))
    const full = `Hello ${ph('PERSON', 1, m)}!`
    reply.push({ kind: 'text', index: 0, text: full.slice(0, 10) }, { kind: 'text', index: 0, text: full.slice(10) })
    const chunks = await collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    expect(textOf(chunks)).toBe('Hello Lars Holm!')
  })

  test('the finished reply, the prompt and a Bash row show real values on screen; a web search row keeps the placeholder', async ($, on) => {
    world(on)
    const stored = await appendPrompt($, 'Lars Holm')
    const p = ph('PERSON', 1, markerIn(JSON.stringify(stored.message?.content)))
    const row = async (component: string, props: object) => {
      const ui = await $.ui.mount({ plugin: 'pii-mask', surface: 'terminal', component, props } as never)
      const drawn = JSON.stringify(await ui.drawn())
      await ui.unmount()
      return drawn
    }
    expect(await row('AssistantMessage', { text: `Hi ${p},`, isFirstOfReply: true })).toContain('Hi Lars Holm,')
    expect(await row('UserMessage', { text: `Write to ${p}`, origin: { kind: 'user' }, isExpanded: false })).toContain('Write to Lars Holm')
    const bash = { tool: 'Bash', input: { command: `echo ${p}` }, isRunning: false, isErrored: false, isInterrupted: false }
    expect(await row('ToolUse', bash)).toContain('echo Lars Holm')
    const web = { tool: 'WebSearch', input: { query: `${p} news` }, isRunning: false, isErrored: false, isInterrupted: false }
    expect(await row('ToolUse', web)).toContain(`${p} news`)
  })

  test('images are removed before they are stored or sent', async ($, on) => {
    world(on)
    const stored = await $.session.append({
      message: { type: 'user', role: 'user', content: [{ type: 'text', text: 'see' }, { type: 'image', source: {} }] },
      door: 'prompt',
      origin: { kind: 'user' },
      uuid: 'i1',
    } as never)
    const content = stored.message?.content as { type: string }[]
    expect(content.some(b => b.type === 'image')).toBe(false)
    expect(JSON.stringify(content)).toContain('removed an image or PDF')
  })

  test('the system prompt is masked, gets the note, and stays the same between requests', async ($, on) => {
    world(on, { sections: [{ id: 'env', text: 'Working on Lars Holm\'s laptop', scope: 'session' }] })
    const a = (await $.prompt.compose(COMPOSE)).sections
    const b = (await $.prompt.compose(COMPOSE)).sections
    expect(JSON.stringify(a)).toBe(JSON.stringify(b))
    expect(a.find(s => s.id === 'env')?.text).not.toContain('Lars')
    expect(a.at(-1)?.id).toBe('pii-mask:note')
  })
})

describe('the local model\'s life cycle', () => {
  test('first use: the setup script runs, nothing is sent, and the prompt is not kept unmasked', async ($, on) => {
    const { seen, clock } = world(on, { installed: false, up: false, setupHangs: true })
    const pending = appendPrompt($, 'Write to Lars Holm')
    await clock.advance(7000)
    const stored = await pending
    expect(JSON.stringify(stored.message?.content)).not.toContain('Lars')
    expect(seen.spawns[0]?.slice(1)).toEqual([expect.stringMatching(/server\/setup\.py$/), '--cache', CACHE])
    const chunks = await collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    expect(textOf(chunks)).toContain('installing its local model')
    expect(textOf(chunks)).toContain('installing PyTorch')
    expect(seen.requestSent).toBe(false)
  })

  test('installed but not running: the mod starts the server and masks as soon as it answers', async ($, on) => {
    const { seen, clock } = world(on, { up: false })
    const pending = appendPrompt($, 'Write to Lars Holm')
    await clock.advance(1000)
    const stored = await pending
    expect(JSON.stringify(stored.message?.content)).not.toContain('Lars')
    expect(JSON.stringify(stored.message?.content)).toContain('PERSON_1')
    const start = seen.runs.find(r => r.some(a => a.endsWith('server/server.py')))
    expect(start?.[0]).toBe(`${CACHE}/venv/bin/python`)
    expect(start).toContain('--daemon')
  })

  test('no Python: every request is stopped with the reason', async ($, on) => {
    const { seen, clock } = world(on, { up: false, installed: false, noPython: true })
    const pending = collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    await clock.advance(7000)
    expect(textOf(await pending)).toContain('Python 3.9 or newer is needed')
    expect(seen.requestSent).toBe(false)
  })

  test('a model call slower than its time limit removes the content instead of sending it', async ($, on) => {
    const { clock } = world(on, { slowMs: 9000 })
    const pending = appendPrompt($, 'Write to Lars Holm')
    await clock.advance(10_000)
    const text = JSON.stringify((await pending).message?.content)
    expect(text).not.toContain('Lars')
    expect(text).toContain('could not check this content')
  })

  test('the same text is sent to the model once', async ($, on) => {
    const { seen } = world(on, { sections: [{ id: 'env', text: 'Lars Holm', scope: 'session' }] })
    await $.prompt.compose(COMPOSE)
    await $.prompt.compose(COMPOSE)
    expect(seen.detects).toBe(1)
  })

  test('an old Claude Code is blocked', async ($, on) => {
    const { seen } = world(on, { version: '2.1.296' })
    const chunks = await collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    expect(textOf(chunks)).toContain('older than 2.1.302')
    expect(seen.requestSent).toBe(false)
  })
})

describe('the pane', () => {
  test('lists every masked value by kind, with its placeholder, on terminal and desktop', async ($, on) => {
    world(on)
    const stored = await appendPrompt($, 'Write to Lars Holm (lars@holm.dk).')
    const m = markerIn(JSON.stringify(stored.message?.content))
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: 'pii-mask', surface, component: 'Pane', props: {} as never, requestId: 'pii-mask' })
      const drawn = JSON.stringify(await ui.drawn())
      expect(drawn).toContain('2 values masked')
      expect(drawn).toContain('People (1)')
      expect(drawn).toContain('Lars Holm')
      expect(drawn).toContain(ph('PERSON', 1, m))
      expect(drawn).toContain('Emails (1)')
      await ui.unmount()
    }
  })

  test('shows an empty state before anything is masked', async ($, on) => {
    world(on)
    const ui = await $.ui.mount({ plugin: 'pii-mask', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'pii-mask' })
    expect(JSON.stringify(await ui.drawn())).toContain('Nothing masked yet')
    await ui.unmount()
  })

  test('/pii-mask opens it; /pii-mask status prints the table', async ($, on) => {
    world(on)
    let opened = ''
    on('ui.open', ($2, e) => {
      opened = e.id
      return { value: { isPlaced: true } } as never
    })
    await appendPrompt($, 'Lars Holm')
    const a = await $.command.run({ command: 'pii-mask', args: '' } as never)
    expect(opened).toBe('pii-mask')
    expect(JSON.stringify(a)).toContain('Opened the pii-mask pane: 1 value(s) masked')
    const b = await $.command.run({ command: 'pii-mask', args: 'status' } as never)
    expect(JSON.stringify(b)).toContain('Values masked: 1')
    expect(JSON.stringify(b)).not.toContain('Lars Holm') // values are only shown in the pane
  })
})

describe('built-in patterns', () => {
  test('catch what has a fixed shape even when the model finds nothing', async ($, on) => {
    world(on, { finds: {} })
    const stored = await appendPrompt($, 'Refund DK50 0040 0440 1162 43, card 4111 1111 1111 1111, mail lars@holm.dk')
    const text = JSON.stringify(stored.message?.content)
    const m = markerIn(text)
    expect(text).toContain(`Refund ${ph('IBAN', 1, m)}, card ${ph('CARD', 1, m)}, mail ${ph('EMAIL', 1, m)}`)
  })
})

describe('pii-mask\'s own messages', () => {
  test('when the model is down, the reason it gives and its status still reach the screen', async ($, on) => {
    const { clock } = world(on, { installed: false, up: false, setupHangs: true })
    const pending = collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    await clock.advance(7000)
    const stop = textOf(await pending)
    expect(stop).toContain('installing its local model')
    // The engine stores the answer as a row, like any reply.
    const reply = await $.session.append({
      message: { type: 'assistant', role: 'assistant', content: [{ type: 'text', text: stop }] },
      door: 'response', origin: { kind: 'model', model: 'claude' }, uuid: 'a1',
    } as never)
    expect(JSON.stringify(reply.message?.content)).toContain('installing its local model')
    // The status command's invocation and output rows too.
    const status = (await $.command.run({ command: 'pii-mask', args: 'status' } as never)) as { text: string }
    for (const text of ['<command-name>/pii-mask</command-name>\n<command-args>status</command-args>', `<local-command-stdout>${status.text}</local-command-stdout>`]) {
      const row = await $.session.append({
        message: { type: 'user', role: 'user', content: [{ type: 'text', text }] },
        door: 'command', origin: { kind: 'user' }, uuid: `c${text.length}`,
      } as never)
      expect(JSON.stringify(row.message?.content)).not.toContain('could not check this content')
    }
  })

  test('a row that mixes pii-mask\'s words with anything else is still checked by the model', async ($, on) => {
    const { clock } = world(on, { installed: false, up: false, setupHangs: true })
    const pending = appendPrompt($, '/pii-mask status\nAnna Berg')
    await clock.advance(7000)
    const text = JSON.stringify((await pending).message?.content)
    expect(text).not.toContain('Anna Berg')
    expect(text).toContain('could not check this content')
  })
})

describe('the panel above the prompt', () => {
  const mountBand = ($: Engine) =>
    $.ui.mount({ plugin: 'pii-mask', surface: 'terminal', component: 'AbovePrompt', props: { hasSurvey: false } as never })

  test('during the first install it shows the step, a progress bar, the MB so far and that nothing is sent', async ($, on) => {
    const { clock } = world(on, { installed: false, up: false, setupHangs: true })
    const pending = appendPrompt($, 'hello')
    await clock.advance(7000)
    await pending
    const ui = await mountBand($)
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn).toContain('installing its local model')
    expect(drawn).toContain('step 2 of 4: installing PyTorch')
    expect(drawn).toContain('█')
    expect(drawn).toContain('412 MB so far')
    expect(drawn).toContain("Nothing is sent to Claude until it's ready")
    await ui.unmount()
  })

  test('when the install fails it shows the reason, the fix and a Retry button', async ($, on) => {
    const { clock } = world(on, {
      installed: false,
      up: false,
      setupFails: { reason: 'this Intel Mac needs Python 3.12 or older for PyTorch, and only 3.13.1 was found.', fix: 'brew install python@3.12' },
    })
    const pending = appendPrompt($, 'hello')
    await clock.advance(7000)
    await pending
    const ui = await mountBand($)
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn).toContain("can't start its local model")
    expect(drawn).toContain('needs Python 3.12 or older')
    expect(drawn).toContain('To fix it: brew install python@3.12')
    expect(await ui.find({ key: 'retry' })).toBeDefined()
    expect(await ui.find({ key: 'details' })).toBeDefined()
    await ui.unmount()
    // The answer to a request says the same.
    const chunks = await collect($.turn.step({ turnId: 't', index: 0, model: 'claude', messageCount: 1 }))
    expect(textOf(chunks)).toContain('To fix it: brew install python@3.12')
  })

  test('no Python at all: the fix names how to install it', async ($, on) => {
    const { clock } = world(on, { installed: false, up: false, noPython: true })
    const pending = appendPrompt($, 'hello')
    await clock.advance(7000)
    await pending
    const ui = await mountBand($)
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn).toContain('Python 3.9 or newer is needed')
    expect(drawn).toMatch(/To fix it: (brew install python@3\.12|sudo apt install python3)/)
    await ui.unmount()
  })

  test('once the model runs, there is no panel', async ($, on) => {
    world(on)
    await appendPrompt($, 'Lars Holm')
    const ui = await mountBand($)
    expect(JSON.stringify(await ui.drawn())).not.toContain('pii-mask')
    await ui.unmount()
  })
})
