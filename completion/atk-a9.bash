# bash-Completion fuer atk-a9. Installation:
#   sudo cp completion/atk-a9.bash /etc/bash_completion.d/atk-a9
# oder von Hand in der aktuellen Shell:
#   source completion/atk-a9.bash

_atk_a9() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local commands="scan status battery dpi polling lod buttons advanced sensor \
fire combo macro angle longrange wheel-guard pair factory-reset probe dump \
export import"
    local global="--device -d --timeout --verbose -v --help -h"

    # Unterkommando finden (erstes Wort nach den globalen Optionen)
    local sub=""
    local i
    for (( i=1; i < cword; i++ )); do
        case "${words[i]}" in
            -*) ;;
            *) sub="${words[i]}"; break ;;
        esac
    done

    if [ -z "$sub" ]; then
        COMPREPLY=( $(compgen -W "$commands $global" -- "$cur") )
        return
    fi

    case "$sub" in
        status)
            COMPREPLY=( $(compgen -W "--json --no-buttons --quick --watch" -- "$cur") ) ;;
        battery)
            COMPREPLY=( $(compgen -W "--json --watch" -- "$cur") ) ;;
        dpi)
            COMPREPLY=( $(compgen -W "set select stages color --select" -- "$cur") ) ;;
        polling)
            COMPREPLY=( $(compgen -W "set 125 250 500 1000 2000 4000 8000" -- "$cur") ) ;;
        lod)
            COMPREPLY=( $(compgen -W "set" -- "$cur") ) ;;
        buttons)
            if [ "$prev" = "buttons" ] || [ "$prev" = "set" ]; then
                COMPREPLY=( $(compgen -W "set" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "mouse dpi hscroll fire shortcut macro polling wheel aus \
left right middle back forward cycle up down" -- "$cur") )
            fi ;;
        advanced)
            COMPREPLY=( $(compgen -W "set --debounce --motion-sync --sleep --linear --ripple" -- "$cur") ) ;;
        sensor)
            COMPREPLY=( $(compgen -W "set basic competitive max" -- "$cur") ) ;;
        fire)
            COMPREPLY=( $(compgen -W "--interval --count" -- "$cur") ) ;;
        macro)
            COMPREPLY=( $(compgen -W "show set clear assign --mode" -- "$cur") ) ;;
        angle)
            COMPREPLY=( $(compgen -W "set --snap" -- "$cur") ) ;;
        longrange)
            COMPREPLY=( $(compgen -W "an aus" -- "$cur") ) ;;
        pair)
            COMPREPLY=( $(compgen -W "status start cancel" -- "$cur") ) ;;
        factory-reset)
            COMPREPLY=( $(compgen -W "--yes" -- "$cur") ) ;;
        export|import)
            if [ "$sub" = "import" ]; then
                COMPREPLY=( $(compgen -W "--dry-run" -- "$cur") )
            fi
            COMPREPLY+=( $(compgen -f -- "$cur") ) ;;
    esac
}

complete -F _atk_a9 atk-a9
