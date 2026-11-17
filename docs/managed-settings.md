# pii-mask with managed settings (Team and Enterprise plans)

## Why

On a Team or Enterprise plan, and on any computer with managed settings, Claude Code puts its built-in security default (`sec-default`) first. It keeps the organisation's rules out of reach of the plugins a person installs, so it lets those plugins see only part of what goes to Claude. You see lines like these when a session starts:

```
pii-mask: its session.append hook was skipped. "cc-plugin-sec-default" runs first and sent the event on past it.
pii-mask: its prompt.context hook was skipped. ...
```

Your prompts and tool results (files Claude reads, command output) are still masked. These aren't:

| What | Hook passed over |
| --- | --- |
| Files you mention with `@` | `prompt.attachment`, `session.append` (attachments) |
| CLAUDE.md and the system prompt | `prompt.context`, `prompt.compose` |
| Reminders and hook output Claude Code adds | `session.append` (attachments, hook output) |

Since 0.7.18 pii-mask notices this at the first request and checks those parts before each request; since 0.7.19 it also shows a note above the prompt, and `/pii-mask fix` gives the steps. A request where they hold personal data is stopped, and you're told which file and what kind of data. The status line says `on; attachments and CLAUDE.md are checked, not masked`. You can then remove it (`/rewind`, or edit the file), or send it unmasked for the session with `/pii-mask allow-unmasked`.

To have them masked instead, list pii-mask in managed settings, as below. It then runs before the security default and sees everything.

## How

**The quick way:** type `/pii-mask fix` in Claude Code. It prints the command for your computer: it creates the file below, or adds the three keys to the one you have and keeps what's in it. Run it in a terminal, then restart Claude Code. The steps below do the same by hand.

Managed settings are a file only an administrator can write. On your own computer that is you, with `sudo`. On a computer your company manages, ask IT to add the lines below.

1. Open, or create, the managed settings file:
   - macOS: `/Library/Application Support/ClaudeCode/managed-settings.json`
   - Linux and WSL: `/etc/claude-code/managed-settings.json`
   - Windows: `C:\Program Files\ClaudeCode\managed-settings.json`

   On macOS:
   ```sh
   sudo mkdir -p "/Library/Application Support/ClaudeCode"
   sudo nano "/Library/Application Support/ClaudeCode/managed-settings.json"
   ```

2. Put this in it. If the file already has settings, add these three keys to them.
   ```json
   {
     "extraKnownMarketplaces": {
       "pii-mask": { "source": { "source": "github", "repo": "majidkabir/pii-mask" } }
     },
     "enabledPlugins": { "pii-mask@pii-mask": true },
     "prependPlugins": ["pii-mask@pii-mask", "sec-default@builtin"]
   }
   ```
   - `prependPlugins` puts pii-mask first: it sees every message before anything else does and every reply after.
   - Keep `sec-default@builtin` in the list. Without it, the security default no longer runs at all.
   - The three keys are all needed. With one missing, pii-mask stays where it was.
   - Settings you set yourself (`~/.claude/settings.json`) can't do this on a Team or Enterprise plan.

3. Restart Claude Code. The "hook was skipped" lines are gone, and `/pii-mask status` no longer mentions the security default.

   To check: start `claude --debug` and look in the debug log for `hooks module pii-mask@pii-mask loaded (…, tier prepend)`. `tier user` means the settings weren't read: check the path and that the JSON is valid (`python3 -m json.tool < file`).

## Good to know

- With managed settings in place, every other plugin you installed yourself runs after the security default, as on a Team or Enterprise plan. Only the plugins in `prependPlugins` run first.
- New pii-mask releases still arrive by themselves: managed settings name the repository, not a version.
- [customer-mask's team rollout](https://github.com/majidkabir/customer-mask/blob/main/docs/team-rollout.md) does the same for a whole team, with a stricter variant that keeps people's own plugins out.
