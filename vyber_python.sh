# Společná funkce: najde vhodný Python s Tkinter.
# Na Apple Silicon (M1–M4…) vybere Python, který běží NATIVNĚ (arm64) – ne přes Rosettu.
# Na Intel Macu dá přednost Tcl/Tk 8.x (jen s ním tam funguje přetahování myší).
# Výsledek: proměnné PY (cesta), PYRUN (příkaz pro spuštění), PYARCH, PYTCL.

vyber_python() {
  PY=""; PYRUN=""; PYARCH=""; PYTCL=""
  local KANDIDATI=(
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
    /Library/Frameworks/Python.framework/Versions/*/bin/python3
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    /usr/bin/python3
  )
  local TEST='import tkinter, platform; print(platform.machine(), tkinter.TclVersion)'
  local C INFO

  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    # Apple Silicon: jen nativní arm64 Python
    for C in "${KANDIDATI[@]}"; do
      [ -x "$C" ] || continue
      INFO="$(arch -arm64 "$C" -c "$TEST" 2>/dev/null)" || continue
      case "$INFO" in arm64*) ;; *) continue ;; esac
      PY="$C"; PYRUN="arch -arm64 $C"; PYARCH="arm64"; PYTCL="${INFO#* }"
      return 0
    done
  fi

  # Intel Mac (nebo na Apple Silicon žádný nativní Python): přednost Tcl/Tk 8.x
  for C in "${KANDIDATI[@]}"; do
    [ -x "$C" ] || continue
    INFO="$("$C" -c "$TEST" 2>/dev/null)" || continue
    [ -n "$INFO" ] || continue
    if [ -z "$PY" ]; then PY="$C"; PYRUN="$C"; PYARCH="${INFO%% *}"; PYTCL="${INFO#* }"; fi
    case "${INFO#* }" in 8.*) PY="$C"; PYRUN="$C"; PYARCH="${INFO%% *}"; PYTCL="${INFO#* }"; break ;; esac
  done
  [ -n "$PY" ]
}
