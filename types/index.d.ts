export type VaultEntry = {
  token: string
  value: string
  label: string
}

/** What the panel above the prompt and the pane show about the local model. */
export type EngineView = {
  /** 'off': the first-time install failed, so nothing is masked until a retry succeeds. */
  state: 'unknown' | 'installing' | 'starting' | 'ready' | 'failed' | 'off'
  /** The install step (1-based) and how many there are; 0 outside the install. */
  step: number
  steps: number
  /** What is happening now, or why it failed. */
  detail: string
  /** A command or action that fixes the failure, when one is known. */
  fix: string
  /** MB downloaded and installed so far (the install folder's size). */
  mb: number
  /** When the current state began, and now (ms), so the elapsed time redraws. */
  since: number
  now: number
  /** Where the install and server logs are. */
  folder: string
  /** The install failed in this session (not remembered from an earlier one), so the panel shows it. */
  fresh?: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'pii-mask': {
      vault: VaultEntry[]
      /** This session's placeholder marker. */
      marker: string | null
      /** The status line's text, which the pane shows too. */
      status: string
      engine: EngineView
    }
  }
}
