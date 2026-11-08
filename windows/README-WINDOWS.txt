Ras for Windows 10 / 11 (64-bit) - paper trading only, no real orders
=======================================================================

INSTALL
1. Right-click Ras-windows.zip > "Extract All...". (Running it from inside the zip won't work.)
2. Open the extracted "Ras" folder and double-click "Install Ras.bat".
   - Windows SmartScreen may warn the file came from the internet: "More info" > "Run anyway".
   - It copies Ras to %LOCALAPPDATA%\Programs\Ras and adds "Ras" to the Start Menu and Desktop.
     No administrator rights needed.
   - The first time, it asks for your Capital.com API key, login email and the key's custom password
     (press Enter to skip any key you don't have). Keys go into Windows Credential Manager, never into files.
3. You can delete the extracted folder and the zip afterwards.

USE
- Open "Ras" from the Start Menu or Desktop. It opens in its own window after a few seconds
  (the first start can take up to a minute).
- The first start also downloads the voice models (~480 MB) in the background; everything else works meanwhile.
- Windows may ask to allow the microphone (for "Hey Jarvis"). If voice stays off: Settings > Privacy & security >
  Microphone > "Let desktop apps access your microphone".
- Close the window to quit; the engine stops by itself about a minute later
  ("Stop Ras.bat" in the install folder stops it immediately).

IF SOMETHING IS WRONG
- Prices stay empty: close Ras, open %LOCALAPPDATA%\Programs\Ras and run "Check Capital.com.bat".
- Change keys: run "Set up keys.bat" in the same folder.
- Logs: %APPDATA%\com.jarvis.station\logs\jarvis.log

UNINSTALL
- Run "Uninstall Ras.bat" in %LOCALAPPDATA%\Programs\Ras. Your journal, settings and keys are kept in
  %APPDATA%\com.jarvis.station (delete that folder too to remove everything).
