<?php
// upload.php - receives either:
// 1) metadata save: POST action=meta&sessionId=...&party=...
// 2) audio chunk upload: multipart with "audio" + sessionId + chunkIndex (+ optional mimeType/token)

header('X-Content-Type-Options: nosniff');

// ---------- helpers ----------
function bad_request($msg, $code = 400) {
  http_response_code($code);
  header('Content-Type: text/plain; charset=utf-8');
  echo $msg;
  exit;
}

function ok_json($arr) {
  header('Content-Type: application/json; charset=utf-8');
  echo json_encode($arr);
  exit;
}

function safe_session_id($raw) {
  $sid = preg_replace('/[^0-9]/', '', $raw ?? '');
  return $sid !== '' ? $sid : 'unknown';
}

function ensure_dir($dir) {
  if (!is_dir($dir)) {
    if (!mkdir($dir, 0777, true)) {
      bad_request('Failed to create uploads directory', 500);
    }
  }
}

function ext_from_mime($mime) {
  $m = strtolower(trim($mime ?? ''));
  if (str_contains($m, 'webm')) return 'webm';
  if (str_contains($m, 'ogg'))  return 'ogg';
  if (str_contains($m, 'mp4'))  return 'mp4';
  // default
  return 'webm';
}

// ---------- optional shared-secret check ----------
// If you want to REQUIRE a token, set $REQUIRED_TOKEN to a non-empty string.
$REQUIRED_TOKEN = '2766'; // e.g. 'my-secret'
if ($REQUIRED_TOKEN !== '') {
  $provided = $_POST['token'] ?? '';
  if (!hash_equals($REQUIRED_TOKEN, $provided)) {
    bad_request('Unauthorized', 401);
  }
}

// ---------- metadata save ----------
if (isset($_POST['action']) && $_POST['action'] === 'meta') {
  $sessionId = safe_session_id($_POST['sessionId'] ?? '');
  $party = $_POST['party'] ?? '';

  $dir = __DIR__ . "/uploads/$sessionId";
  ensure_dir($dir);

  if (file_put_contents($dir . '/party.txt', $party) === false) {
    bad_request('Failed to write party.txt', 500);
  }

  ok_json([ 'ok' => true ]);
}

// ---------- audio upload ----------
if (!isset($_FILES['audio'])) {
  // Common causes:
  // - exceeded post_max_size/upload_max_filesize
  // - server blocked large multipart
  // - incorrect field name
  $postMax = ini_get('post_max_size');
  $uploadMax = ini_get('upload_max_filesize');
  bad_request("No audio uploaded. Check PHP limits (post_max_size=$postMax, upload_max_filesize=$uploadMax).", 400);
}

$err = $_FILES['audio']['error'] ?? UPLOAD_ERR_OK;
if ($err !== UPLOAD_ERR_OK) {
  // Give a readable error.
  $map = [
    UPLOAD_ERR_INI_SIZE => 'File exceeds upload_max_filesize',
    UPLOAD_ERR_FORM_SIZE => 'File exceeds MAX_FILE_SIZE',
    UPLOAD_ERR_PARTIAL => 'File only partially uploaded',
    UPLOAD_ERR_NO_FILE => 'No file uploaded',
    UPLOAD_ERR_NO_TMP_DIR => 'Missing temp folder',
    UPLOAD_ERR_CANT_WRITE => 'Failed to write to disk',
    UPLOAD_ERR_EXTENSION => 'Upload stopped by extension',
  ];
  $msg = $map[$err] ?? ('Upload error code ' . $err);
  bad_request($msg, 400);
}

$sessionId = safe_session_id($_POST['sessionId'] ?? '');
$index = intval($_POST['chunkIndex'] ?? 0);
$mimeType = $_POST['mimeType'] ?? '';

$dir = __DIR__ . "/uploads/$sessionId";
ensure_dir($dir);

$ext = ext_from_mime($mimeType);
$filename = 'chunk_' . str_pad((string)$index, 4, '0', STR_PAD_LEFT) . '.' . $ext;
$dest = $dir . '/' . $filename;

$tmp = $_FILES['audio']['tmp_name'] ?? '';
if ($tmp === '' || !is_uploaded_file($tmp)) {
  bad_request('Upload temp file missing', 400);
}

if (!move_uploaded_file($tmp, $dest)) {
  bad_request('Failed to move uploaded file', 500);
}

// Build a relative URL for the client.
$relUrl = "./uploads/$sessionId/$filename";

ok_json([
  'ok' => true,
  'filename' => $filename,
  'url' => $relUrl,
]);
