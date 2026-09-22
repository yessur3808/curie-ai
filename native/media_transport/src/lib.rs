//! Curie's bounded byte-oriented audio and attachment transport.
//!
//! Python retains connector presentation, model inference, transcription,
//! personality, and policy. This module owns constant-memory inspection,
//! hashing, PCM/WAV concatenation, and cancellable subprocess supervision.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tempfile::NamedTempFile;

const TRANSPORT_VERSION: &str = "rust-media-transport-v1";
const COPY_BUFFER_BYTES: usize = 64 * 1024;
const SIGNATURE_BYTES: usize = 4096;

type InspectionTuple = (
    String,
    u64,
    String,
    String,
    String,
    String,
    bool,
    String,
    String,
    bool,
);
type WavChunk = (u64, u64, u64, f64);
type WavManifest = (u32, u16, u16, u64, Vec<WavChunk>);

#[derive(Clone, Debug)]
struct Inspection {
    safe_name: String,
    size_bytes: u64,
    sha256: String,
    detected_mime: &'static str,
    detected_kind: &'static str,
    signature: &'static str,
    signature_confident: bool,
    declared_kind: &'static str,
    extension_kind: &'static str,
    mismatch: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WavFormat {
    audio_format: u16,
    channels: u16,
    sample_rate: u32,
    byte_rate: u32,
    block_align: u16,
    bits_per_sample: u16,
}

#[derive(Clone, Copy, Debug)]
struct WavSource {
    format: WavFormat,
    data_offset: u64,
    data_len: u64,
}

#[derive(Debug)]
struct ProcessOutcome {
    return_code: i32,
    stdout: Vec<u8>,
    timed_out: bool,
    cancelled: bool,
    stdout_truncated: bool,
    duration_ms: f64,
}

#[derive(Debug)]
enum StreamMessage {
    Line(Vec<u8>),
    Error(String),
    Eof,
}

#[pyclass(skip_from_py_object)]
#[derive(Clone)]
struct CancellationToken {
    cancelled: Arc<AtomicBool>,
}

#[pymethods]
impl CancellationToken {
    #[new]
    fn new() -> Self {
        Self {
            cancelled: Arc::new(AtomicBool::new(false)),
        }
    }

    fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
}

#[pyclass(skip_from_py_object)]
struct StreamingProcess {
    child: Arc<Mutex<std::process::Child>>,
    receiver: Arc<Mutex<Receiver<StreamMessage>>>,
    deadline: Instant,
}

fn stream_stdout_lines(
    stdout: std::process::ChildStdout,
    sender: std::sync::mpsc::SyncSender<StreamMessage>,
    max_line_bytes: usize,
) {
    let mut reader = BufReader::with_capacity(COPY_BUFFER_BYTES, stdout);
    loop {
        let mut line = Vec::with_capacity(max_line_bytes.min(8192));
        let mut overflow = false;
        let mut reached_eof = false;
        loop {
            let available = match reader.fill_buf() {
                Ok(value) => value,
                Err(_) => {
                    let _ = sender.send(StreamMessage::Error(
                        "media_worker_stdout_failed".to_owned(),
                    ));
                    return;
                }
            };
            if available.is_empty() {
                reached_eof = true;
                break;
            }
            let consumed = available
                .iter()
                .position(|byte| *byte == b'\n')
                .map_or(available.len(), |position| position + 1);
            let remaining = max_line_bytes.saturating_sub(line.len());
            if remaining > 0 {
                line.extend_from_slice(&available[..consumed.min(remaining)]);
            }
            if consumed > remaining {
                overflow = true;
            }
            let has_newline = available[..consumed].ends_with(b"\n");
            reader.consume(consumed);
            if has_newline {
                break;
            }
        }
        if overflow {
            let _ = sender.send(StreamMessage::Error("media_worker_stdout_limit".to_owned()));
            return;
        }
        if !line.is_empty() && sender.send(StreamMessage::Line(line)).is_err() {
            return;
        }
        if reached_eof {
            let _ = sender.send(StreamMessage::Eof);
            return;
        }
    }
}

fn terminate_child(child: &Arc<Mutex<std::process::Child>>) -> i32 {
    let mut process = child
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    match process.try_wait() {
        Ok(Some(status)) => status.code().unwrap_or(-1),
        _ => {
            let _ = process.kill();
            process
                .wait()
                .ok()
                .and_then(|status| status.code())
                .unwrap_or(-1)
        }
    }
}

#[pymethods]
impl StreamingProcess {
    #[new]
    #[pyo3(signature = (command, stdin_data, *, cwd=None, environment=Vec::new(), timeout_ms=120_000, max_line_bytes=262_144, queue_capacity=64))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        command: Vec<String>,
        stdin_data: &Bound<'_, PyBytes>,
        cwd: Option<String>,
        environment: Vec<(String, String)>,
        timeout_ms: u64,
        max_line_bytes: usize,
        queue_capacity: usize,
    ) -> PyResult<Self> {
        if command.is_empty() || command[0].trim().is_empty() {
            return Err(PyValueError::new_err("media_worker_command_empty"));
        }
        let mut builder = Command::new(&command[0]);
        builder.args(&command[1..]);
        if let Some(directory) = cwd {
            builder.current_dir(directory);
        }
        builder.env_clear();
        builder.envs(environment);
        builder
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut process = builder
            .spawn()
            .map_err(|_| PyRuntimeError::new_err("media_worker_spawn_failed"))?;
        let input = stdin_data.as_bytes().to_vec();
        let mut stdin = process.stdin.take();
        thread::spawn(move || {
            if let Some(mut handle) = stdin.take() {
                let _ = handle.write_all(&input);
                let _ = handle.flush();
            }
        });
        let stdout = process
            .stdout
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("media_worker_stdout_unavailable"))?;
        let (sender, receiver) = sync_channel(queue_capacity.clamp(1, 1024));
        thread::spawn(move || stream_stdout_lines(stdout, sender, max_line_bytes.max(1)));
        Ok(Self {
            child: Arc::new(Mutex::new(process)),
            receiver: Arc::new(Mutex::new(receiver)),
            deadline: Instant::now() + Duration::from_millis(timeout_ms.max(1)),
        })
    }

    fn read_line(&self, py: Python<'_>) -> PyResult<Option<Py<PyBytes>>> {
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            terminate_child(&self.child);
            return Err(PyRuntimeError::new_err("media_worker_timeout"));
        }
        let receiver = Arc::clone(&self.receiver);
        let message = py.detach(move || {
            receiver
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .recv_timeout(remaining)
        });
        match message {
            Ok(StreamMessage::Line(line)) => Ok(Some(PyBytes::new(py, &line).unbind())),
            Ok(StreamMessage::Error(code)) => Err(PyRuntimeError::new_err(code)),
            Ok(StreamMessage::Eof) | Err(RecvTimeoutError::Disconnected) => Ok(None),
            Err(RecvTimeoutError::Timeout) => {
                terminate_child(&self.child);
                Err(PyRuntimeError::new_err("media_worker_timeout"))
            }
        }
    }

    fn wait(&self, py: Python<'_>) -> PyResult<i32> {
        let child = Arc::clone(&self.child);
        let deadline = self.deadline;
        py.detach(move || loop {
            let mut process = child
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            match process.try_wait() {
                Ok(Some(status)) => return Ok(status.code().unwrap_or(-1)),
                Ok(None) if Instant::now() < deadline => {
                    drop(process);
                    thread::sleep(Duration::from_millis(10));
                }
                Ok(None) => {
                    let _ = process.kill();
                    let _ = process.wait();
                    return Err(PyRuntimeError::new_err("media_worker_timeout"));
                }
                Err(_) => return Err(PyRuntimeError::new_err("media_worker_wait_failed")),
            }
        })
    }

    fn cancel(&self, py: Python<'_>) -> i32 {
        let child = Arc::clone(&self.child);
        py.detach(move || terminate_child(&child))
    }
}

impl Drop for StreamingProcess {
    fn drop(&mut self) {
        terminate_child(&self.child);
    }
}

fn safe_filename(path: &Path, filename: &str) -> String {
    let selected = if filename.trim().is_empty() {
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("")
    } else {
        Path::new(filename)
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("")
    };
    selected.to_owned()
}

fn suffix_kind(filename: &str) -> &'static str {
    let suffix = Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    match suffix.as_str() {
        "jpg" | "jpeg" | "png" | "webp" | "gif" | "bmp" => "image",
        "mp3" | "wav" | "ogg" | "oga" | "opus" | "m4a" | "flac" | "webm" => "audio",
        "txt" | "md" | "csv" | "json" | "log" | "py" | "js" | "ts" | "html" | "xml" | "pdf"
        | "docx" | "odt" => "document",
        "apk" | "app" | "bat" | "cmd" | "com" | "dll" | "dmg" | "exe" | "iso" | "jar" | "msi"
        | "ps1" | "scr" | "sh" => "executable",
        _ => "unknown",
    }
}

fn mime_kind(content_type: &str) -> &'static str {
    let mime = content_type.trim().to_ascii_lowercase();
    if mime.starts_with("image/") {
        "image"
    } else if mime.starts_with("audio/") {
        "audio"
    } else if mime.starts_with("text/")
        || matches!(
            mime.as_str(),
            "application/pdf"
                | "application/json"
                | "application/xml"
                | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                | "application/vnd.oasis.opendocument.text"
        )
    {
        "document"
    } else if matches!(
        mime.as_str(),
        "application/x-dosexec"
            | "application/x-executable"
            | "application/x-sharedlib"
            | "application/java-archive"
    ) {
        "executable"
    } else {
        "unknown"
    }
}

fn looks_textual(bytes: &[u8]) -> bool {
    if bytes.is_empty() || std::str::from_utf8(bytes).is_err() {
        return false;
    }
    let controls = bytes
        .iter()
        .filter(|byte| **byte < 0x20 && !matches!(**byte, b'\n' | b'\r' | b'\t' | 0x0c))
        .count();
    controls * 100 <= bytes.len()
}

fn sniff_signature(bytes: &[u8]) -> (&'static str, &'static str, &'static str, bool) {
    if bytes.starts_with(b"MZ") {
        return ("application/x-dosexec", "executable", "pe", true);
    }
    if bytes.starts_with(b"\x7fELF") {
        return ("application/x-executable", "executable", "elf", true);
    }
    if bytes.starts_with(b"#!") {
        return ("application/x-executable", "executable", "script", true);
    }
    if bytes.len() >= 4
        && matches!(
            &bytes[..4],
            [0xfe, 0xed, 0xfa, 0xce]
                | [0xfe, 0xed, 0xfa, 0xcf]
                | [0xce, 0xfa, 0xed, 0xfe]
                | [0xcf, 0xfa, 0xed, 0xfe]
        )
    {
        return ("application/x-executable", "executable", "mach-o", true);
    }
    if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WAVE" {
        return ("audio/wav", "audio", "wav", true);
    }
    if bytes.starts_with(b"OggS") {
        return ("audio/ogg", "audio", "ogg", true);
    }
    if bytes.starts_with(b"fLaC") {
        return ("audio/flac", "audio", "flac", true);
    }
    if bytes.starts_with(b"ID3")
        || (bytes.len() >= 2 && bytes[0] == 0xff && bytes[1] & 0xe0 == 0xe0)
    {
        return ("audio/mpeg", "audio", "mp3", true);
    }
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" {
        return ("audio/mp4", "audio", "iso-bmff", true);
    }
    if bytes.starts_with(&[0x1a, 0x45, 0xdf, 0xa3]) {
        return ("audio/webm", "audio", "ebml", true);
    }
    if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
        return ("image/jpeg", "image", "jpeg", true);
    }
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return ("image/png", "image", "png", true);
    }
    if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        return ("image/gif", "image", "gif", true);
    }
    if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" {
        return ("image/webp", "image", "webp", true);
    }
    if bytes.starts_with(b"BM") {
        return ("image/bmp", "image", "bmp", true);
    }
    if bytes.starts_with(b"%PDF-") {
        return ("application/pdf", "document", "pdf", true);
    }
    if bytes.starts_with(b"PK\x03\x04") || bytes.starts_with(b"PK\x05\x06") {
        return ("application/zip", "archive", "zip", true);
    }
    if looks_textual(bytes) {
        return ("text/plain", "document", "text", false);
    }
    ("application/octet-stream", "unknown", "unknown", false)
}

fn hex_digest(bytes: impl AsRef<[u8]>) -> String {
    bytes
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn inspect_core(
    path: &str,
    filename: &str,
    content_type: &str,
    max_bytes: u64,
    strict: bool,
) -> Result<Inspection, String> {
    let source = Path::new(path);
    let metadata = fs::symlink_metadata(source).map_err(|_| "attachment_not_regular")?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("attachment_not_regular".to_owned());
    }
    let size = metadata.len();
    if size == 0 {
        return Err("attachment_empty".to_owned());
    }
    if size > max_bytes {
        return Err("attachment_too_large".to_owned());
    }
    let file = File::open(source).map_err(|_| "attachment_unreadable")?;
    let opened = file.metadata().map_err(|_| "attachment_unreadable")?;
    if !opened.is_file() || opened.len() != size {
        return Err("attachment_changed_during_inspection".to_owned());
    }
    let mut reader = BufReader::with_capacity(COPY_BUFFER_BYTES, file);
    let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
    let mut signature = Vec::with_capacity(SIGNATURE_BYTES);
    let mut digest = Sha256::new();
    let mut total = 0_u64;
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|_| "attachment_unreadable")?;
        if read == 0 {
            break;
        }
        total = total.saturating_add(read as u64);
        if total > max_bytes {
            return Err("attachment_too_large".to_owned());
        }
        digest.update(&buffer[..read]);
        if signature.len() < SIGNATURE_BYTES {
            let needed = SIGNATURE_BYTES - signature.len();
            signature.extend_from_slice(&buffer[..read.min(needed)]);
        }
    }
    if total != size {
        return Err("attachment_changed_during_inspection".to_owned());
    }
    let (detected_mime, detected_kind, signature_name, signature_confident) =
        sniff_signature(&signature);
    let safe_name = safe_filename(source, filename);
    let extension_kind = suffix_kind(&safe_name);
    let declared_kind = mime_kind(content_type);
    if detected_kind == "executable" {
        return Err("media_signature_executable".to_owned());
    }
    let expected_kind = if declared_kind != "unknown" {
        declared_kind
    } else {
        extension_kind
    };
    let mismatch = signature_confident
        && expected_kind != "unknown"
        && detected_kind != "unknown"
        && detected_kind != "archive"
        && expected_kind != detected_kind;
    if strict && mismatch {
        return Err("media_signature_mismatch".to_owned());
    }
    Ok(Inspection {
        safe_name,
        size_bytes: total,
        sha256: hex_digest(digest.finalize()),
        detected_mime,
        detected_kind,
        signature: signature_name,
        signature_confident,
        declared_kind,
        extension_kind,
        mismatch,
    })
}

fn read_u16(value: &[u8]) -> u16 {
    u16::from_le_bytes([value[0], value[1]])
}

fn read_u32(value: &[u8]) -> u32 {
    u32::from_le_bytes([value[0], value[1], value[2], value[3]])
}

fn parse_wav(path: &Path, max_bytes: u64) -> Result<WavSource, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "wav_not_regular")?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("wav_not_regular".to_owned());
    }
    if metadata.len() < 44 || metadata.len() > max_bytes {
        return Err("wav_size_invalid".to_owned());
    }
    let mut file = BufReader::new(File::open(path).map_err(|_| "wav_unreadable")?);
    let mut header = [0_u8; 12];
    file.read_exact(&mut header)
        .map_err(|_| "wav_header_invalid")?;
    if &header[..4] != b"RIFF" || &header[8..] != b"WAVE" {
        return Err("wav_header_invalid".to_owned());
    }
    let mut format = None;
    let mut data = None;
    loop {
        let mut chunk = [0_u8; 8];
        if file.read_exact(&mut chunk).is_err() {
            break;
        }
        let len = read_u32(&chunk[4..8]) as u64;
        let offset = file.stream_position().map_err(|_| "wav_unreadable")?;
        let padded = len.saturating_add(len & 1);
        if offset.saturating_add(padded) > metadata.len() {
            return Err("wav_chunk_invalid".to_owned());
        }
        if &chunk[..4] == b"fmt " {
            if len < 16 {
                return Err("wav_format_invalid".to_owned());
            }
            let mut raw = [0_u8; 16];
            file.read_exact(&mut raw)
                .map_err(|_| "wav_format_invalid")?;
            let parsed = WavFormat {
                audio_format: read_u16(&raw[0..2]),
                channels: read_u16(&raw[2..4]),
                sample_rate: read_u32(&raw[4..8]),
                byte_rate: read_u32(&raw[8..12]),
                block_align: read_u16(&raw[12..14]),
                bits_per_sample: read_u16(&raw[14..16]),
            };
            if !matches!(parsed.audio_format, 1 | 3)
                || parsed.channels == 0
                || parsed.sample_rate == 0
                || parsed.block_align == 0
                || parsed.bits_per_sample == 0
            {
                return Err("wav_format_unsupported".to_owned());
            }
            format = Some(parsed);
        } else if &chunk[..4] == b"data" {
            data = Some((offset, len));
        }
        file.seek(SeekFrom::Start(offset.saturating_add(padded)))
            .map_err(|_| "wav_unreadable")?;
    }
    let format = format.ok_or_else(|| "wav_format_missing".to_owned())?;
    let (data_offset, data_len) = data.ok_or_else(|| "wav_data_missing".to_owned())?;
    if data_len == 0 || data_len % u64::from(format.block_align) != 0 {
        return Err("wav_data_invalid".to_owned());
    }
    Ok(WavSource {
        format,
        data_offset,
        data_len,
    })
}

fn write_wav_header(
    writer: &mut impl Write,
    format: WavFormat,
    data_len: u32,
) -> std::io::Result<()> {
    writer.write_all(b"RIFF")?;
    writer.write_all(&(36_u32.saturating_add(data_len)).to_le_bytes())?;
    writer.write_all(b"WAVEfmt ")?;
    writer.write_all(&16_u32.to_le_bytes())?;
    writer.write_all(&format.audio_format.to_le_bytes())?;
    writer.write_all(&format.channels.to_le_bytes())?;
    writer.write_all(&format.sample_rate.to_le_bytes())?;
    writer.write_all(&format.byte_rate.to_le_bytes())?;
    writer.write_all(&format.block_align.to_le_bytes())?;
    writer.write_all(&format.bits_per_sample.to_le_bytes())?;
    writer.write_all(b"data")?;
    writer.write_all(&data_len.to_le_bytes())?;
    Ok(())
}

fn copy_exact(source: &Path, offset: u64, len: u64, writer: &mut impl Write) -> Result<(), String> {
    let mut file = BufReader::with_capacity(
        COPY_BUFFER_BYTES,
        File::open(source).map_err(|_| "wav_unreadable")?,
    );
    file.seek(SeekFrom::Start(offset))
        .map_err(|_| "wav_unreadable")?;
    let mut remaining = len;
    let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
    while remaining > 0 {
        let requested = remaining.min(buffer.len() as u64) as usize;
        let read = file
            .read(&mut buffer[..requested])
            .map_err(|_| "wav_unreadable")?;
        if read == 0 {
            return Err("wav_data_truncated".to_owned());
        }
        writer
            .write_all(&buffer[..read])
            .map_err(|_| "wav_output_failed")?;
        remaining -= read as u64;
    }
    Ok(())
}

fn concat_wav_core(
    sources: Vec<String>,
    destination: String,
    max_bytes: u64,
) -> Result<(u64, u64, u32, u16, u16), String> {
    if sources.is_empty() {
        return Err("wav_sources_empty".to_owned());
    }
    let mut parsed = Vec::with_capacity(sources.len());
    let mut total = 0_u64;
    let mut expected = None;
    for source in &sources {
        let info = parse_wav(Path::new(source), max_bytes)?;
        if let Some(format) = expected {
            if format != info.format {
                return Err("wav_format_mismatch".to_owned());
            }
        } else {
            expected = Some(info.format);
        }
        total = total
            .checked_add(info.data_len)
            .ok_or_else(|| "wav_output_too_large".to_owned())?;
        if total.saturating_add(44) > max_bytes || total > u32::MAX as u64 {
            return Err("wav_output_too_large".to_owned());
        }
        parsed.push(info);
    }
    let format = expected.ok_or_else(|| "wav_sources_empty".to_owned())?;
    let target = PathBuf::from(destination);
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    let temporary = NamedTempFile::new_in(parent).map_err(|_| "wav_output_failed")?;
    let mut writer = BufWriter::with_capacity(COPY_BUFFER_BYTES, temporary.as_file());
    write_wav_header(&mut writer, format, total as u32).map_err(|_| "wav_output_failed")?;
    for (source, info) in sources.iter().zip(parsed.iter()) {
        copy_exact(
            Path::new(source),
            info.data_offset,
            info.data_len,
            &mut writer,
        )?;
    }
    writer.flush().map_err(|_| "wav_output_failed")?;
    drop(writer);
    temporary
        .persist(&target)
        .map_err(|_| "wav_output_failed")?;
    Ok((
        total.saturating_add(44),
        total / u64::from(format.block_align),
        format.sample_rate,
        format.channels,
        format.bits_per_sample,
    ))
}

fn wav_manifest_core(
    path: String,
    max_chunk_ms: u64,
    max_bytes: u64,
) -> Result<WavManifest, String> {
    let source = parse_wav(Path::new(&path), max_bytes)?;
    let total_frames = source.data_len / u64::from(source.format.block_align);
    let target_frames = (u64::from(source.format.sample_rate) * max_chunk_ms.max(1) / 1000).max(1);
    let target_bytes = target_frames
        .saturating_mul(u64::from(source.format.block_align))
        .max(u64::from(source.format.block_align));
    let mut chunks = Vec::new();
    let mut consumed = 0_u64;
    while consumed < source.data_len {
        let length = target_bytes.min(source.data_len - consumed);
        let frames = length / u64::from(source.format.block_align);
        chunks.push((
            source.data_offset + consumed,
            length,
            frames,
            frames as f64 / f64::from(source.format.sample_rate) * 1000.0,
        ));
        consumed += length;
    }
    Ok((
        source.format.sample_rate,
        source.format.channels,
        source.format.bits_per_sample,
        total_frames,
        chunks,
    ))
}

fn run_process_core(
    command: Vec<String>,
    stdin_data: Vec<u8>,
    cwd: Option<String>,
    environment: Vec<(String, String)>,
    timeout_ms: u64,
    max_stdout_bytes: usize,
    cancelled: Arc<AtomicBool>,
) -> Result<ProcessOutcome, String> {
    if command.is_empty() || command[0].trim().is_empty() {
        return Err("media_worker_command_empty".to_owned());
    }
    let started = Instant::now();
    let mut builder = Command::new(&command[0]);
    builder.args(&command[1..]);
    if let Some(directory) = cwd {
        builder.current_dir(directory);
    }
    builder.env_clear();
    builder.envs(environment);
    builder
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = builder
        .spawn()
        .map_err(|_| "media_worker_spawn_failed".to_owned())?;
    let mut stdin = child.stdin.take();
    let writer = thread::spawn(move || {
        if let Some(mut handle) = stdin.take() {
            let _ = handle.write_all(&stdin_data);
            let _ = handle.flush();
        }
    });
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "media_worker_stdout_unavailable".to_owned())?;
    let reader = thread::spawn(move || {
        let mut handle = BufReader::with_capacity(COPY_BUFFER_BYTES, stdout);
        let mut output = Vec::with_capacity(max_stdout_bytes.min(COPY_BUFFER_BYTES));
        let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
        let mut truncated = false;
        loop {
            match handle.read(&mut buffer) {
                Ok(0) => break,
                Ok(read) => {
                    let remaining = max_stdout_bytes.saturating_sub(output.len());
                    if remaining > 0 {
                        output.extend_from_slice(&buffer[..read.min(remaining)]);
                    }
                    if read > remaining {
                        truncated = true;
                    }
                }
                Err(_) => break,
            }
        }
        (output, truncated)
    });
    let deadline = started + Duration::from_millis(timeout_ms.max(1));
    let mut timed_out = false;
    let mut was_cancelled = false;
    let status = loop {
        if cancelled.load(Ordering::Acquire) {
            was_cancelled = true;
            let _ = child.kill();
            break child.wait().map_err(|_| "media_worker_wait_failed")?;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            let _ = child.kill();
            break child.wait().map_err(|_| "media_worker_wait_failed")?;
        }
        match child.try_wait().map_err(|_| "media_worker_wait_failed")? {
            Some(value) => break value,
            None => thread::sleep(Duration::from_millis(10)),
        }
    };
    let _ = writer.join();
    let (stdout, stdout_truncated) = reader
        .join()
        .map_err(|_| "media_worker_stdout_failed".to_owned())?;
    Ok(ProcessOutcome {
        return_code: status.code().unwrap_or(-1),
        stdout,
        timed_out,
        cancelled: was_cancelled,
        stdout_truncated,
        duration_ms: started.elapsed().as_secs_f64() * 1000.0,
    })
}

#[pyfunction(signature = (path, filename="", content_type="", max_bytes=20_971_520, strict=true))]
fn inspect_media(
    py: Python<'_>,
    path: String,
    filename: &str,
    content_type: &str,
    max_bytes: u64,
    strict: bool,
) -> PyResult<InspectionTuple> {
    let filename = filename.to_owned();
    let content_type = content_type.to_owned();
    let inspection = py
        .detach(move || inspect_core(&path, &filename, &content_type, max_bytes, strict))
        .map_err(PyValueError::new_err)?;
    Ok((
        inspection.safe_name,
        inspection.size_bytes,
        inspection.sha256,
        inspection.detected_mime.to_owned(),
        inspection.detected_kind.to_owned(),
        inspection.signature.to_owned(),
        inspection.signature_confident,
        inspection.declared_kind.to_owned(),
        inspection.extension_kind.to_owned(),
        inspection.mismatch,
    ))
}

#[pyfunction(signature = (sources, destination, *, max_bytes=268_435_456))]
fn concatenate_pcm_wav(
    py: Python<'_>,
    sources: Vec<String>,
    destination: String,
    max_bytes: u64,
) -> PyResult<(u64, u64, u32, u16, u16)> {
    py.detach(move || concat_wav_core(sources, destination, max_bytes))
        .map_err(PyValueError::new_err)
}

#[pyfunction(signature = (path, *, max_chunk_ms=1_000, max_bytes=268_435_456))]
fn wav_chunk_manifest(
    py: Python<'_>,
    path: String,
    max_chunk_ms: u64,
    max_bytes: u64,
) -> PyResult<WavManifest> {
    py.detach(move || wav_manifest_core(path, max_chunk_ms, max_bytes))
        .map_err(PyValueError::new_err)
}

#[pyfunction(signature = (command, stdin_data, *, cwd=None, environment=Vec::new(), timeout_ms=120_000, max_stdout_bytes=1_048_576, cancellation=None))]
#[allow(clippy::too_many_arguments)]
fn run_supervised(
    py: Python<'_>,
    command: Vec<String>,
    stdin_data: &Bound<'_, PyBytes>,
    cwd: Option<String>,
    environment: Vec<(String, String)>,
    timeout_ms: u64,
    max_stdout_bytes: usize,
    cancellation: Option<PyRef<'_, CancellationToken>>,
) -> PyResult<(i32, Py<PyBytes>, bool, bool, bool, f64)> {
    let input = stdin_data.as_bytes().to_vec();
    let flag = cancellation
        .as_ref()
        .map(|token| Arc::clone(&token.cancelled))
        .unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
    let result = py
        .detach(move || {
            run_process_core(
                command,
                input,
                cwd,
                environment,
                timeout_ms,
                max_stdout_bytes,
                flag,
            )
        })
        .map_err(PyRuntimeError::new_err)?;
    Ok((
        result.return_code,
        PyBytes::new(py, &result.stdout).unbind(),
        result.timed_out,
        result.cancelled,
        result.stdout_truncated,
        result.duration_ms,
    ))
}

#[pyfunction]
fn transport_version() -> &'static str {
    TRANSPORT_VERSION
}

#[pymodule]
fn _curie_media_transport(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<CancellationToken>()?;
    module.add_class::<StreamingProcess>()?;
    module.add_function(wrap_pyfunction!(inspect_media, module)?)?;
    module.add_function(wrap_pyfunction!(concatenate_pcm_wav, module)?)?;
    module.add_function(wrap_pyfunction!(wav_chunk_manifest, module)?)?;
    module.add_function(wrap_pyfunction!(run_supervised, module)?)?;
    module.add_function(wrap_pyfunction!(transport_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn wav_bytes(samples: &[i16]) -> Vec<u8> {
        let format = WavFormat {
            audio_format: 1,
            channels: 1,
            sample_rate: 16_000,
            byte_rate: 32_000,
            block_align: 2,
            bits_per_sample: 16,
        };
        let mut output = Vec::new();
        write_wav_header(&mut output, format, (samples.len() * 2) as u32).unwrap();
        for sample in samples {
            output.extend_from_slice(&sample.to_le_bytes());
        }
        output
    }

    #[test]
    fn signatures_cover_supported_media_and_executables() {
        assert_eq!(sniff_signature(b"RIFF\0\0\0\0WAVE").2, "wav");
        assert_eq!(sniff_signature(b"OggS\0\0").2, "ogg");
        assert_eq!(sniff_signature(b"%PDF-1.7").2, "pdf");
        assert_eq!(sniff_signature(b"MZpayload").1, "executable");
    }

    #[test]
    fn canonical_wav_header_has_expected_size() {
        let bytes = wav_bytes(&[1, -1, 2, -2]);
        assert_eq!(bytes.len(), 52);
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(read_u32(&bytes[40..44]), 8);
        let mut cursor = Cursor::new(bytes);
        cursor.seek(SeekFrom::Start(44)).unwrap();
        let mut sample = [0_u8; 2];
        cursor.read_exact(&mut sample).unwrap();
        assert_eq!(i16::from_le_bytes(sample), 1);
    }
}
