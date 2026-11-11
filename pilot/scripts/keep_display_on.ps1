# Keep the display on while the rig runs, without changing any power setting.
# Asks Windows for "display required" (as a video player does). Stopping this process releases it.
Add-Type -Namespace Rig -Name Power -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
[void][Rig.Power]::SetThreadExecutionState([uint32]"0x80000003")
while ($true) { Start-Sleep -Seconds 300 }
