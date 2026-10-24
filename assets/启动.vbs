' szu-netlogin launcher
' 1) clear the "downloaded from Internet" mark (MOTW) for all files in this folder
' 2) create the process through WMI so ShellExecute's publisher prompt is skipped
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)

On Error Resume Next
ps = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""Get-ChildItem -LiteralPath '" & base & "' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File"""
sh.Run ps, 0, True
On Error GoTo 0

cmd = Chr(34) & base & "\szu-netlogin.exe" & Chr(34)
For Each a In WScript.Arguments
  cmd = cmd & " " & a
Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
wmi.Get("Win32_Process").Create cmd, base, Null, Null
