' Hidden-window wrapper for scripts/collect_and_push.sh (Windows Task Scheduler).
'
' Why a .vbs: Task Scheduler pointed at bash.exe flashes a console window at
' every run. wscript.exe running this file shows no window at all (Run arg 0).
' Third Run arg True = wait for exit, and the exit code is passed through with
' WScript.Quit — without it Task Scheduler records every run as success.
'
' Register (repo path is derived from this file's location, nothing to edit):
'   schtasks /Create /TN memtrail-daily-collect /SC DAILY /ST 12:30 /F ^
'     /TR "wscript.exe \"<repo>\scripts\run_collect_and_push_hidden.vbs\""
' Then in taskschd.msc enable "Run task as soon as possible after a scheduled
' start is missed" (StartWhenAvailable) — README §6: 노트북 잡이 꺼진 시간대에
' 걸려 있으면 매번 놓친다.
'
' Extra arguments are passed through to collect_and_push.sh (e.g. --force,
' --no-publish).
'
' mkdir -p logs must come first: logs/ is gitignored, so on a fresh clone the
' redirect itself would fail before the script ever starts.
Dim fso, root, args, i, cmd, rc
Set fso = CreateObject("Scripting.FileSystemObject")
' repo root = parent of the scripts/ folder this file lives in
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
root = Replace(root, "\", "/")
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " " & WScript.Arguments(i)
Next
cmd = """C:\Program Files\Git\bin\bash.exe"" -lc " & _
      """cd '" & root & "' && mkdir -p logs && " & _
      "bash scripts/collect_and_push.sh" & args & " >> logs/daily_collect.log 2>&1"""
rc = CreateObject("WScript.Shell").Run(cmd, 0, True)
WScript.Quit rc
