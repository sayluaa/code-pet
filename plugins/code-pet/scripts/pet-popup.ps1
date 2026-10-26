param([string]$Data)

# 코드 펫 팝업: 화면 오른쪽 위, 포커스를 가져오지 않고 몇 초 뒤 사라진다. 클릭하면 바로 닫힌다.
# GIF 가 있으면 움직이는 그림, 없으면 텍스트 이모티콘을 보여준다.
# 이미 떠 있으면 새 창을 쌓지 않고, popup.json 의 id 가 바뀐 것을 보고 내용만 갱신한다.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -WarningAction SilentlyContinue -ReferencedAssemblies System.Windows.Forms -TypeDefinition @"
using System.Windows.Forms;
public class PetToastForm : Form {
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

$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ClaudePetPopup', [ref]$created)
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

function Read-Pet {
    try { return [IO.File]::ReadAllText($Data, [Text.Encoding]::UTF8) | ConvertFrom-Json } catch { return $null }
}

$pet = Read-Pet
if (-not $pet) { $mutex.ReleaseMutex(); exit 0 }

$radius = 15
$bg     = [System.Drawing.Color]::FromArgb(32, 33, 38)
$accent = [System.Drawing.Color]::FromArgb(217, 119, 87)
$W = 380; $H = 128

# 이벤트 핸들러는 $script: 스코프를 못 읽으므로 바뀌는 값은 해시테이블에 둔다
$state = @{ Pet = $pet; Id = "$($pet.id)"; Born = [Diagnostics.Stopwatch]::StartNew(); Img = $null }

$form = New-Object PetToastForm
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.ShowInTaskbar = $false
$form.TopMost = $true
$form.BackColor = $bg
$form.ClientSize = New-Object System.Drawing.Size($W, $H)
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
# 화면 오른쪽 위 (파일 저장 토스트는 오른쪽 아래라 겹치지 않는다)
$form.Location = New-Object System.Drawing.Point(($wa.Right - $W - 16), ($wa.Top + 16))
$form.Region = New-Object System.Drawing.Region((New-RoundPath (New-Object System.Drawing.RectangleF(0, 0, $W, $H)) $radius))

$pic = New-Object System.Windows.Forms.PictureBox
$pic.SetBounds(14, 16, 96, 96)
$pic.SizeMode = 'Zoom'
$pic.BackColor = $bg
$pic.Visible = $false
$form.Controls.Add($pic)

$faceFont  = New-Object System.Drawing.Font('Malgun Gothic', 14, [System.Drawing.FontStyle]::Bold)
$titleFont = New-Object System.Drawing.Font('Malgun Gothic', 10, [System.Drawing.FontStyle]::Bold)
$subFont   = New-Object System.Drawing.Font('Malgun Gothic', 9)
$tf = [System.Windows.Forms.TextFormatFlags]

function Set-Pet($p) {
    $state.Pet = $p
    $state.Id = "$($p.id)"
    $state.Born.Restart()
    if ($state.Img) { $pic.Image = $null; $state.Img.Dispose(); $state.Img = $null }
    $pic.Visible = $false
    if ($p.gif -and (Test-Path $p.gif)) {
        try {
            $state.Img = [System.Drawing.Image]::FromFile($p.gif)
            $pic.Image = $state.Img
            $pic.Visible = $true
        } catch {}
    }
    $form.Invalidate()
}

$form.Add_Paint({
    param($s, $e)
    $g = $e.Graphics
    $g.SmoothingMode = 'AntiAlias'
    $path = New-RoundPath (New-Object System.Drawing.RectangleF(0.5, 0.5, ($W - 2), ($H - 2))) $radius
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(80, 82, 92), 1)
    $g.DrawPath($pen, $path)
    $p = $state.Pet
    if (-not $pic.Visible) {
        $fl = $tf::HorizontalCenter -bor $tf::VerticalCenter -bor $tf::WordBreak
        [System.Windows.Forms.TextRenderer]::DrawText($g, [string]$p.face, $faceFont, (New-Object System.Drawing.Rectangle(10, 16, 104, 96)), $accent, $fl)
    }
    $tl = $tf::Left -bor $tf::Top -bor $tf::EndEllipsis
    [System.Windows.Forms.TextRenderer]::DrawText($g, [string]$p.title, $titleFont, (New-Object System.Drawing.Rectangle(124, 16, ($W - 138), 24)), [System.Drawing.Color]::White, $tl)
    $ml = $tf::Left -bor $tf::Top -bor $tf::WordBreak
    [System.Windows.Forms.TextRenderer]::DrawText($g, [string]$p.message, $subFont, (New-Object System.Drawing.Rectangle(124, 44, ($W - 138), ($H - 54))), [System.Drawing.Color]::FromArgb(190, 192, 200), $ml)
    $pen.Dispose(); $path.Dispose()
})

$form.Add_Click({ $form.Close() })
$pic.Add_Click({ $form.Close() })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 150
$timer.Add_Tick({
    $now = Read-Pet
    if ($now -and ("$($now.id)" -ne $state.Id)) { Set-Pet $now }
    $secs = 5
    if ($state.Pet.seconds) { $secs = [int]$state.Pet.seconds }
    if ($state.Born.ElapsedMilliseconds -ge ($secs * 1000)) { $form.Close() }
})

Set-Pet $pet
$timer.Start()
[System.Windows.Forms.Application]::Run($form)
$timer.Dispose()
if ($state.Img) { $state.Img.Dispose() }
$form.Dispose()
$mutex.ReleaseMutex()
$mutex.Dispose()
