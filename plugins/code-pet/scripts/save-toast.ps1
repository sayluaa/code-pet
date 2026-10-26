param([string]$Name, [string]$Status)

# 파일 저장 알림 토스트: 화면 오른쪽 아래, 포커스를 가져오지 않고 3초 뒤 사라진다.
# 연속으로 저장되면 새 창을 쌓지 않고 떠 있는 토스트의 내용만 갱신한다.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -WarningAction SilentlyContinue -ReferencedAssemblies System.Windows.Forms -TypeDefinition @"
using System.Windows.Forms;
public class SaveToastForm : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get {
            CreateParams cp = base.CreateParams;
            cp.ExStyle |= 0x80 | 0x08000000 | 0x8;   // TOOLWINDOW | NOACTIVATE | TOPMOST
            return cp;
        }
    }
}
"@

$life   = 3000   # 표시 시간(ms)
$radius = 15     # 창 모서리 둥글기
$msgFile = Join-Path $env:TEMP 'claude-save-toast.txt'

# 최신 메시지를 파일에 남기고, 이미 떠 있는 토스트가 있으면 그쪽이 읽어 가도록 하고 종료
$noBom = New-Object Text.UTF8Encoding($false)
try { [IO.File]::WriteAllText($msgFile, "$Name`n$Status", $noBom) } catch {}
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ClaudeSaveToast', [ref]$created)
if (-not $created) { exit 0 }

function New-RoundPath([System.Drawing.RectangleF]$r, [float]$rad) {
    $d = $rad * 2
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddArc($r.X, $r.Y, $d, $d, 180, 90)
    $p.AddArc($r.Right - $d, $r.Y, $d, $d, 270, 90)
    $p.AddArc($r.Right - $d, $r.Bottom - $d, $d, $d, 0, 90)
    $p.AddArc($r.X, $r.Bottom - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    return $p
}

$bg     = [System.Drawing.Color]::FromArgb(32, 33, 38)
$accent = [System.Drawing.Color]::FromArgb(217, 119, 87)
$W = 300; $H = 72

$state = @{ Name = $Name; Status = $Status; Born = [Diagnostics.Stopwatch]::StartNew(); Last = "$Name`n$Status" }

$form = New-Object SaveToastForm
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.ShowInTaskbar = $false
$form.TopMost = $true
$form.BackColor = $bg
$form.ClientSize = New-Object System.Drawing.Size($W, $H)
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
# 화면 오른쪽 위. 펫 팝업(pet-popup.ps1: 높이 128, 위 여백 16)이 떠 있으면 그 바로 아래에 붙는다
$petH = 128
$gap  = 8
function Get-ToastY {
    $m = $null
    if ([System.Threading.Mutex]::TryOpenExisting('Local\ClaudePetPopup', [ref]$m)) {
        $m.Dispose()
        return ($wa.Top + 16 + $petH + $gap)
    }
    return ($wa.Top + 16)
}
$form.Location = New-Object System.Drawing.Point(($wa.Right - $W - 16), (Get-ToastY))
$form.Region = New-Object System.Drawing.Region((New-RoundPath (New-Object System.Drawing.RectangleF(0, 0, $W, $H)) $radius))

$titleFont = New-Object System.Drawing.Font('Malgun Gothic', 10, [System.Drawing.FontStyle]::Bold)
$subFont   = New-Object System.Drawing.Font('Malgun Gothic', 9)
$flags = [System.Windows.Forms.TextFormatFlags]::Left -bor [System.Windows.Forms.TextFormatFlags]::VerticalCenter -bor [System.Windows.Forms.TextFormatFlags]::EndEllipsis

$form.Add_Paint({
    param($s, $e)
    $g = $e.Graphics
    $g.SmoothingMode = 'AntiAlias'
    $path = New-RoundPath (New-Object System.Drawing.RectangleF(0.5, 0.5, ($W - 2), ($H - 2))) $radius
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(80, 82, 92), 1)
    $g.DrawPath($pen, $path)
    $brush = New-Object System.Drawing.SolidBrush($accent)
    $g.FillEllipse($brush, 18, 20, 10, 10)
    [System.Windows.Forms.TextRenderer]::DrawText($g, '저장됨  ' + $state.Name, $titleFont, (New-Object System.Drawing.Rectangle(38, 10, ($W - 52), 28)), [System.Drawing.Color]::White, $flags)
    [System.Windows.Forms.TextRenderer]::DrawText($g, $state.Status, $subFont, (New-Object System.Drawing.Rectangle(38, 38, ($W - 52), 24)), [System.Drawing.Color]::FromArgb(190, 192, 200), $flags)
    $pen.Dispose(); $brush.Dispose(); $path.Dispose()
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 150
$timer.Add_Tick({
    try {
        $now = [IO.File]::ReadAllText($msgFile, [Text.Encoding]::UTF8)
        if ($now -ne $state.Last) {
            $parts = $now -split "`n", 2
            $state.Last = $now
            $state.Name = $parts[0]
            $state.Status = if ($parts.Count -gt 1) { $parts[1] } else { '' }
            $state.Born.Restart()
            $form.Invalidate()
        }
    } catch {}
    # 펫 팝업이 나중에 떠도 겹치지 않게 위치를 계속 맞춘다
    $y = Get-ToastY
    if ($form.Top -ne $y) { $form.Top = $y }
    if ($state.Born.ElapsedMilliseconds -ge $life) { $form.Close() }
})
$timer.Start()

[System.Windows.Forms.Application]::Run($form)
$timer.Dispose()
$form.Dispose()
$mutex.ReleaseMutex()
$mutex.Dispose()
