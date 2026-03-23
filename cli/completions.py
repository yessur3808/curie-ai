# cli/completions.py
"""
Shell completion script generator for the `curie` CLI.

Usage:
  source <(curie completions bash)
  curie completions zsh > ~/.zfunc/_curie
  curie completions fish > ~/.config/fish/completions/curie.fish

Supported shells: bash, zsh, fish
"""

from __future__ import annotations

import sys


# ─── Bash ─────────────────────────────────────────────────────────────────────

_BASH_COMPLETION = """\
# Curie AI – bash completion
# Add to ~/.bashrc:  source <(curie completions bash)

_curie_completions() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local top_commands="start stop restart status metrics tasks agent doctor service logs onboard channel cron memory auth completions"

    if [ $cword -eq 1 ]; then
        COMPREPLY=($(compgen -W "$top_commands" -- "$cur"))
        return
    fi

    case "${words[1]}" in
        start|restart)
            COMPREPLY=($(compgen -W "--api --telegram --discord --all" -- "$cur"))
            ;;
        metrics)
            COMPREPLY=($(compgen -W "--once --interval" -- "$cur"))
            ;;
        tasks)
            COMPREPLY=($(compgen -W "--live --all" -- "$cur"))
            ;;
        agent)
            COMPREPLY=($(compgen -W "-m --message --no-api --api-url" -- "$cur"))
            ;;
        doctor)
            COMPREPLY=($(compgen -W "--verbose" -- "$cur"))
            ;;
        service)
            COMPREPLY=($(compgen -W "install start stop restart status" -- "$cur"))
            ;;
        logs)
            COMPREPLY=($(compgen -W "-n --lines -f --follow" -- "$cur"))
            ;;
        channel)
            COMPREPLY=($(compgen -W "list doctor bind-telegram bind-discord" -- "$cur"))
            ;;
        cron)
            COMPREPLY=($(compgen -W "list add remove enable disable" -- "$cur"))
            ;;
        memory)
            COMPREPLY=($(compgen -W "list get stats clear-user" -- "$cur"))
            ;;
        auth)
            case "$prev" in
                --provider)
                    COMPREPLY=($(compgen -W "openai anthropic gemini llama.cpp" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "login status use --provider" -- "$cur"))
                    ;;
            esac
            ;;
        completions)
            COMPREPLY=($(compgen -W "bash zsh fish" -- "$cur"))
            ;;
    esac
}

complete -F _curie_completions curie
"""


# ─── Zsh ──────────────────────────────────────────────────────────────────────

_ZSH_COMPLETION = """\
#compdef curie
# Curie AI – zsh completion
# Add to ~/.zshrc:  fpath=(~/.zfunc $fpath)  then run: curie completions zsh > ~/.zfunc/_curie
# Or: source <(curie completions zsh)

_curie() {
    local state

    _arguments -C \\
        '1: :->command' \\
        '*: :->args'

    case $state in
        command)
            local commands=(
                'start:Start Curie daemon in background'
                'stop:Stop the running daemon'
                'restart:Restart the daemon'
                'status:Show daemon/agent status'
                'metrics:Live system metrics dashboard'
                'tasks:Show task and sub-agent breakdown'
                'agent:Chat with Curie (interactive or single message)'
                'doctor:Run system diagnostics'
                'service:Manage Curie as an OS service'
                'logs:Show / follow daemon log output'
                'onboard:Guided first-time setup wizard'
                'channel:Manage chat channel connectors'
                'cron:Manage scheduled prompt jobs'
                'memory:Inspect and manage user memory'
                'auth:Manage LLM provider credentials'
                'completions:Generate shell completion scripts'
            )
            _describe 'curie commands' commands
            ;;
        args)
            case ${words[2]} in
                start|restart)
                    _arguments \\
                        '--api[Enable API connector]' \\
                        '--telegram[Enable Telegram connector]' \\
                        '--discord[Enable Discord connector]' \\
                        '--all[Enable all connectors]'
                    ;;
                metrics)
                    _arguments \\
                        '--once[One-shot snapshot]' \\
                        '--interval[Refresh interval in seconds]:seconds:'
                    ;;
                tasks)
                    _arguments \\
                        '--live[Live-updating view]' \\
                        '--all[Include finished tasks]'
                    ;;
                agent)
                    _arguments \\
                        '-m[Single message]:message:' \\
                        '--message[Single message]:message:' \\
                        '--no-api[Force in-process mode]' \\
                        '--api-url[API base URL]:url:'
                    ;;
                doctor)
                    _arguments '--verbose[Show extra detail]'
                    ;;
                service)
                    local actions=(install start stop restart status)
                    _describe 'service actions' actions
                    ;;
                logs)
                    _arguments \\
                        '-n[Number of lines]:n:' \\
                        '--lines[Number of lines]:n:' \\
                        '-f[Follow log output]' \\
                        '--follow[Follow log output]'
                    ;;
                channel)
                    local ch_cmds=(list doctor bind-telegram bind-discord)
                    _describe 'channel subcommands' ch_cmds
                    ;;
                cron)
                    local cron_cmds=(list add remove enable disable)
                    _describe 'cron subcommands' cron_cmds
                    ;;
                memory)
                    local mem_cmds=(list get stats clear-user)
                    _describe 'memory subcommands' mem_cmds
                    ;;
                auth)
                    local auth_cmds=(login status use)
                    _describe 'auth subcommands' auth_cmds
                    ;;
                completions)
                    local shells=(bash zsh fish)
                    _describe 'shells' shells
                    ;;
            esac
            ;;
    esac
}

_curie "$@"
"""


# ─── Fish ─────────────────────────────────────────────────────────────────────

_FISH_COMPLETION = """\
# Curie AI – fish completion
# Install: curie completions fish > ~/.config/fish/completions/curie.fish

set -l curie_commands start stop restart status metrics tasks agent doctor service logs onboard channel cron memory auth completions

complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a start       -d 'Start Curie daemon'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a stop        -d 'Stop the daemon'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a restart     -d 'Restart the daemon'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a status      -d 'Show daemon status'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a metrics     -d 'Live system metrics'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a tasks       -d 'Task / sub-agent view'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a agent       -d 'Chat with Curie'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a doctor      -d 'System diagnostics'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a service     -d 'OS service management'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a logs        -d 'Show daemon logs'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a onboard     -d 'First-time setup wizard'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a channel     -d 'Manage channels'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a cron        -d 'Scheduled prompt jobs'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a memory      -d 'Inspect user memory'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a auth        -d 'LLM provider auth'
complete -c curie -f -n "not __fish_seen_subcommand_from $curie_commands" -a completions -d 'Shell completions'

# start / restart flags
for _sub in start restart
    complete -c curie -f -n "__fish_seen_subcommand_from $_sub" -l api       -d 'Enable API connector'
    complete -c curie -f -n "__fish_seen_subcommand_from $_sub" -l telegram  -d 'Enable Telegram connector'
    complete -c curie -f -n "__fish_seen_subcommand_from $_sub" -l discord   -d 'Enable Discord connector'
    complete -c curie -f -n "__fish_seen_subcommand_from $_sub" -l all       -d 'Enable all connectors'
end

# service subcommands
complete -c curie -f -n "__fish_seen_subcommand_from service" -a "install start stop restart status"

# channel subcommands
complete -c curie -f -n "__fish_seen_subcommand_from channel" -a "list doctor bind-telegram bind-discord"

# cron subcommands
complete -c curie -f -n "__fish_seen_subcommand_from cron" -a "list add remove enable disable"

# memory subcommands
complete -c curie -f -n "__fish_seen_subcommand_from memory" -a "list get stats clear-user"

# auth subcommands
complete -c curie -f -n "__fish_seen_subcommand_from auth" -a "login status use"
complete -c curie -f -n "__fish_seen_subcommand_from auth" -l provider -a "openai anthropic gemini llama.cpp" -d 'Provider name'

# completions subcommands
complete -c curie -f -n "__fish_seen_subcommand_from completions" -a "bash zsh fish"
"""


# ─── Public function ──────────────────────────────────────────────────────────

_SCRIPTS = {
    "bash": _BASH_COMPLETION,
    "zsh": _ZSH_COMPLETION,
    "fish": _FISH_COMPLETION,
}


def cmd_completions(shell: str) -> int:
    """Print shell completion script to stdout."""
    shell = shell.lower()
    script = _SCRIPTS.get(shell)
    if script is None:
        print(f"Unsupported shell: {shell!r}. Supported: {', '.join(_SCRIPTS)}", file=sys.stderr)
        return 1
    print(script, end="")
    return 0
