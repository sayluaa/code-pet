param([string]$Payload)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$timeout   = 10   # 자동으로 닫히기까지의 초
$radius    = 15   # 창 모서리 둥글기(border-radius)
$boxRadius = 10   # 요약 박스 모서리 둥글기
$soundFile = 'C:\Windows\Media\Windows Notify System Generic.wav'   # 알림창이 뜰 때 재생할 효과음 (없으면 시스템 기본음)

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

# ── 0. 펫 정보: pet.py 가 토큰을 집계하고 마무리 표정을 정한다 (실패해도 알림창은 그대로 뜬다) ──
$petPy   = Join-Path $PSScriptRoot 'pet.py'
# 펫 데이터 폴더 (pet.py 와 같은 규칙)
$configHome = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$dataDir = if ($env:CODE_PET_DATA) { $env:CODE_PET_DATA } else { Join-Path $configHome 'code-pet' }
$petStop = Join-Path $dataDir 'stop.json'
$pet = $null
if ((Test-Path $petPy) -and $Payload -and (Test-Path $Payload)) {
    try {
        Remove-Item $petStop -ErrorAction SilentlyContinue
        & python $petPy stop $Payload 2>$null | Out-Null
        if (Test-Path $petStop) { $pet = [IO.File]::ReadAllText($petStop, [Text.Encoding]::UTF8) | ConvertFrom-Json }
    } catch {}
}

# ── 1. 마지막 답변 텍스트 얻기 (훅 입력 JSON → last_assistant_message, 없으면 transcript) ──
$answer = ''
if ($Payload -and (Test-Path $Payload)) {
    try {
        $in = [IO.File]::ReadAllText($Payload, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $answer = [string]$in.last_assistant_message
        if (-not $answer -and $in.transcript_path -and (Test-Path $in.transcript_path)) {
            $lines = [IO.File]::ReadAllLines($in.transcript_path, [Text.Encoding]::UTF8) | Select-Object -Last 200
            foreach ($line in $lines) {
                if ($line -notmatch '"type":"assistant"') { continue }
                $e = $line | ConvertFrom-Json
                $t = ($e.message.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join "`n"
                if ($t) { $answer = $t }
            }
        }
    } catch {}
    Remove-Item $Payload -ErrorAction SilentlyContinue
}
$answer = $answer.Trim()

# ── 2. 요약(키워드): 답변 마지막의 "[요약] 키워드 · 키워드" 줄을 바로 읽는다 (CLAUDE.md 규칙) ──
# 줄이 없으면 답변 첫 줄 일부를 대신 보여준다
function Get-Summary([string]$text) {
    if (-not $text) { return '답변 완료' }
    $m = [regex]::Matches($text, '(?m)^\s*\[요약\]\s*(.+?)\s*$')
    if ($m.Count -gt 0) {
        $kw = $m[$m.Count - 1].Groups[1].Value.Trim()
        if ($kw.Length -gt 60) { $kw = $kw.Substring(0, 60) + '…' }
        if ($kw) { return $kw }
    }
    $first = ($text -split "`n")[0].Trim()
    if ($first.Length -gt 40) { $first = $first.Substring(0, 40) + '…' }
    return $first
}

# 이벤트 핸들러는 $script: 스코프를 못 읽으므로 공유 값은 해시테이블에 둔다
$state = @{ Remain = $timeout; Tick = 0; Summary = (Get-Summary $answer) }

# ── 3. 둥근 알림창 (위에서부터: 제목 → 펫 → 펫의 한마디 → 요약 박스 → 토큰 → 확인 버튼) ──
$bg     = [System.Drawing.Color]::FromArgb(32, 33, 38)
$boxBg  = [System.Drawing.Color]::FromArgb(48, 50, 58)
$accent = [System.Drawing.Color]::FromArgb(217, 119, 87)
$W = 360

$gif = ''
$petH = 0
if ($pet) {
    if ($pet.gif -and (Test-Path $pet.gif)) { $gif = [string]$pet.gif; $petH = 96 } else { $petH = 44 }
}

$y = 18
$titleY = $y;           $y += 32
$petY = $y
if ($pet) { $y += $petH + 2; $speechY = $y; $y += 30 }
$boxY = $y;             $y += 70 + 10
if ($pet) { $tokenY = $y; $y += 22 + 8 }
$btnY = $y
$H = $btnY + 38 + 17

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.ClientSize = New-Object System.Drawing.Size($W, $H)
$form.BackColor = $bg
$form.Font = New-Object System.Drawing.Font('Malgun Gothic', 10)
$form.Region = New-Object System.Drawing.Region((New-RoundPath (New-Object System.Drawing.RectangleF(0, 0, $W, $H)) $radius))

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Claude 답변이 완료되었습니다'
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font('Malgun Gothic', 11, [System.Drawing.FontStyle]::Bold)
$title.TextAlign = 'MiddleCenter'
$title.SetBounds(15, $titleY, ($W - 30), 28)
$form.Controls.Add($title)

if ($pet) {
    if ($gif) {
        # GIF 는 PictureBox 가 알아서 움직여 준다
        $pic = New-Object System.Windows.Forms.PictureBox
        $pic.SetBounds((($W - 96) / 2), $petY, 96, 96)
        $pic.SizeMode = 'Zoom'
        $pic.BackColor = $bg
        try { $pic.Image = [System.Drawing.Image]::FromFile($gif) } catch {}
        $form.Controls.Add($pic)
    } else {
        $face = New-Object System.Windows.Forms.Label
        $face.Text = [string]$pet.face
        $face.ForeColor = $accent
        $face.Font = New-Object System.Drawing.Font('Malgun Gothic', 18, [System.Drawing.FontStyle]::Bold)
        $face.TextAlign = 'MiddleCenter'
        $face.SetBounds(15, $petY, ($W - 30), $petH)
        $form.Controls.Add($face)
    }

    $speech = New-Object System.Windows.Forms.Label
    $speech.Text = [string]$pet.speech
    $speech.ForeColor = [System.Drawing.Color]::White
    $speech.Font = New-Object System.Drawing.Font('Malgun Gothic', 10, [System.Drawing.FontStyle]::Bold)
    $speech.TextAlign = 'MiddleCenter'
    $speech.SetBounds(15, $speechY, ($W - 30), 26)
    $form.Controls.Add($speech)

    $token = New-Object System.Windows.Forms.Label
    $token.Text = '토큰  오늘 ' + $pet.today + ' / 총 ' + $pet.total
    $token.ForeColor = [System.Drawing.Color]::FromArgb(190, 192, 200)
    $token.Font = New-Object System.Drawing.Font('Malgun Gothic', 9)
    $token.TextAlign = 'MiddleCenter'
    $token.SetBounds(15, $tokenY, ($W - 30), 22)
    $form.Controls.Add($token)
}

# 요약 박스 (키워드)
$box = New-Object System.Windows.Forms.Panel
$box.SetBounds(20, $boxY, ($W - 40), 70)
$box.BackColor = $bg
$box.Add_Paint({
    param($s, $e)
    $e.Graphics.SmoothingMode = 'AntiAlias'
    $rect = New-Object System.Drawing.RectangleF(0.5, 0.5, ($s.Width - 2), ($s.Height - 2))
    $path = New-RoundPath $rect $boxRadius
    $brush = New-Object System.Drawing.SolidBrush($boxBg)
    $e.Graphics.FillPath($brush, $path)
    $pen = New-Object System.Drawing.Pen($accent, 1)
    $e.Graphics.DrawPath($pen, $path)
    $flags = [System.Windows.Forms.TextFormatFlags]::HorizontalCenter -bor [System.Windows.Forms.TextFormatFlags]::VerticalCenter -bor [System.Windows.Forms.TextFormatFlags]::WordBreak
    $inner = New-Object System.Drawing.Rectangle(12, 6, ($s.Width - 24), ($s.Height - 12))
    [System.Windows.Forms.TextRenderer]::DrawText($e.Graphics, $state.Summary, $form.Font, $inner, [System.Drawing.Color]::White, $flags)
    $brush.Dispose(); $pen.Dispose(); $path.Dispose()
})
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = '확인'
$ok.FlatStyle = 'Flat'
$ok.FlatAppearance.BorderSize = 0
$ok.BackColor = $accent
$ok.ForeColor = [System.Drawing.Color]::White
$ok.Cursor = [System.Windows.Forms.Cursors]::Hand
$ok.SetBounds((($W - 120) / 2), $btnY, 120, 38)
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$ok.Region = New-Object System.Drawing.Region((New-RoundPath (New-Object System.Drawing.RectangleF(0, 0, 120, 38)) 12))
$form.Controls.Add($ok)
$form.AcceptButton = $ok
$form.CancelButton = $ok
$ok.Text = "확인 ($($state.Remain))"

# 창 외곽선(둥근 테두리)
$form.Add_Paint({
    param($s, $e)
    $e.Graphics.SmoothingMode = 'AntiAlias'
    $path = New-RoundPath (New-Object System.Drawing.RectangleF(0.5, 0.5, ($W - 2), ($H - 2))) $radius
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(80, 82, 92), 1)
    $e.Graphics.DrawPath($pen, $path)
    $pen.Dispose(); $path.Dispose()
})

# 0.2초마다 호출, 5번(=1초)마다 카운트다운
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    $state.Tick++
    if ($state.Tick % 5 -eq 0) {
        $state.Remain--
        if ($state.Remain -le 0) { $form.Close() }
        else { $ok.Text = "확인 ($($state.Remain))" }
    }
})
$timer.Start()

# 창이 뜰 때 효과음 재생 (비동기라 창을 막지 않는다)
$player = $null
if (Test-Path $soundFile) { $player = New-Object System.Media.SoundPlayer($soundFile) }
$form.Add_Shown({
    $form.Activate()
    try {
        if ($player) { $player.Play() } else { [System.Media.SystemSounds]::Asterisk.Play() }
    } catch {}
})
[void]$form.ShowDialog()
$timer.Dispose()
$form.Dispose()
