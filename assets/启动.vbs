' szu-netlogin launcher: uses WMI CreateProcess to avoid the
' "unknown publisher" prompt that ShellExecute shows for unsigned exe files.
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = Chr(34) & base & "\szu-netlogin.exe" & Chr(34)
For Each a In WScript.Arguments
  cmd = cmd & " " & a
Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
wmi.Get("Win32_Process").Create cmd, base, Null, Null
