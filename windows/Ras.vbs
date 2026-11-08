' Ras (portable) - double-click to start. Starts the local backend hidden and opens Ras as an app window.
' Your data lives in %APPDATA%\com.jarvis.station. Closing the window stops Ras about a minute later.
Option Explicit
Dim sh, fs, here, home, env, i, url, edge, candidates, c
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
here = fs.GetParentFolderName(WScript.ScriptFullName)
home = sh.ExpandEnvironmentStrings("%APPDATA%") & "\com.jarvis.station"
If Not fs.FolderExists(home) Then fs.CreateFolder home
Set env = sh.Environment("Process")
env("JARVIS_HOME") = home
env("JARVIS_EXIT_WHEN_IDLE") = "1"
url = "http://127.0.0.1:18765/"

If Not BackendUp() Then
  sh.Run """" & here & "\runtime\python\pythonw.exe"" """ & here & "\runtime\app\run_backend.py""", 0, False
  sh.Popup "Starting Ras... (the first start takes up to a minute)", 4, "Ras", 64
  For i = 1 To 150
    If BackendUp() Then Exit For
    WScript.Sleep 1000
  Next
  If Not BackendUp() Then
    MsgBox "Ras's backend didn't start. See " & home & "\logs\jarvis.log", 16, "Ras"
    WScript.Quit 1
  End If
End If

' Edge (built into Windows 10/11) in app mode looks like a normal app window; else the default browser
candidates = Array(sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Microsoft\Edge\Application\msedge.exe", _
                   sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Microsoft\Edge\Application\msedge.exe")
edge = ""
For Each c In candidates
  If fs.FileExists(c) Then edge = c
Next
If edge <> "" Then
  sh.Run """" & edge & """ --app=" & url & " --window-size=1440,920 --user-data-dir=""" & home & "\window""", 1, False
Else
  sh.Run url
End If

Function BackendUp()
  Dim http
  On Error Resume Next
  Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  http.setTimeouts 800, 800, 800, 800
  http.Open "GET", "http://127.0.0.1:18765/health", False
  http.Send
  BackendUp = (Err.Number = 0 And http.Status = 200)
  On Error GoTo 0
End Function
