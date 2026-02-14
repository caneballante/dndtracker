<?php
// /dnd/sessions.php
$base = __DIR__ . "/uploads";
$baseUrl = "./uploads";

function h($s) { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

$sessions = [];
if (is_dir($base)) {
  foreach (scandir($base) as $sid) {
    if ($sid === "." || $sid === "..") continue;
    $path = $base . "/" . $sid;
    if (is_dir($path) && preg_match('/^\d+$/', $sid)) {
      $sessions[] = $sid;
    }
  }
}
rsort($sessions); // newest first

$selected = $_GET["s"] ?? ($sessions[0] ?? null);
$chunks = [];

if ($selected && preg_match('/^\d+$/', $selected)) {
  $dir = $base . "/" . $selected;
  if (is_dir($dir)) {
    foreach (glob($dir . "/chunk_*.*") as $file) {
    $chunks[] = basename($file);
    }
    sort($chunks);
  }
}
?>
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>D&D Audio Sessions</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-4">
    <h1 class="h4 mb-3">D&D Audio Sessions</h1>

    <?php if (!$sessions): ?>
      <div class="alert alert-warning">No sessions found yet in /dnd/uploads</div>
    <?php else: ?>
      <div class="row g-3">
        <div class="col-12 col-md-4">
          <div class="card">
            <div class="card-header">Sessions</div>
            <div class="list-group list-group-flush">
              <?php foreach ($sessions as $sid): ?>
               <a class="list-group-item list-group-item-action <?php if ($sid === $selected) echo "active"; ?>"
                 href="?s=<?=h($sid)?>">
                 <?=h($sid)?>
              </a>
              <?php endforeach; ?>
            </div>
          </div>
        </div>

        <div class="col-12 col-md-8">
          <div class="card">
            <div class="card-header">
              Session: <?=h($selected)?> (<?=count($chunks)?> chunks)
            </div>
            <div class="card-body">
              <?php if (!$chunks): ?>
                <div class="alert alert-secondary mb-0">No chunks found for this session.</div>
              <?php else: ?>
                <?php foreach ($chunks as $c): ?>
                  <div class="mb-3">
                    <div class="small text-muted mb-1"><?=h($c)?></div>
                    <audio controls preload="none" style="width:100%;">
                      <source src="<?=h($baseUrl . "/" . $selected . "/" . $c)?>" type="audio/webm">
                    </audio>
                    <div class="mt-1">
                      <a class="small" target="_blank" href="<?=h($baseUrl . "/" . $selected . "/" . $c)?>">Open file</a>
                    </div>
                  </div>
                <?php endforeach; ?>
              <?php endif; ?>
            </div>
          </div>
        </div>
      </div>
    <?php endif; ?>
  </div>
</body>
</html>
