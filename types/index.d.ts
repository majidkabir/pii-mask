export type VaultEntry = {
  token: string
  value: string
  label: string
}

declare module 'claude-code' {
  interface PluginState {
    'pii-mask': {
      vault: VaultEntry[]
      /** This session's placeholder marker. */
      marker: string | null
    }
  }
}
