// PhotoBridge by Yundera - Frontend

let state = {
    currentStep: 1,
    connected: false,
    immichUrl: '',
    apiKey: '',
    sourceType: 'google-photos',
    selectedFiles: [],
    validationResult: null,
    jobId: null,
    eventSource: null,
    timerInterval: null,
    importStartTime: null,
    uploadDir: null,
    uploadedFiles: [],
    uploading: false,
    uploadPaused: false,
    currentXhr: null,
};

// ─── Initialization ──────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    // Load saved credentials or default config
    const savedUrl = localStorage.getItem('pb_immich_url');
    const savedKey = localStorage.getItem('pb_api_key');
    if (savedUrl) {
        document.getElementById('immich-url').value = savedUrl;
    } else {
        try {
            const resp = await fetch('/api/config');
            const config = await resp.json();
            document.getElementById('immich-url').value = config.immich_url || '';
        } catch (e) {
            // Use placeholder defaults
        }
    }
    if (savedKey) {
        document.getElementById('api-key').value = savedKey;
    }

    // Source type toggle
    document.querySelectorAll('.source-option').forEach(el => {
        el.addEventListener('click', () => {
            document.querySelectorAll('.source-option').forEach(s => s.classList.remove('selected'));
            el.classList.add('selected');
            el.querySelector('input[type="radio"]').checked = true;
            state.sourceType = el.dataset.source;
            state.selectedFiles = [];
            state.uploadedFiles = [];
            state.uploadDir = null;
            updateFileSelection();
            updateValidateButton();
            updateSourceUI();
        });
    });

    // Direct upload: drag & drop
    const dropZone = document.getElementById('drop-zone');
    if (dropZone) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            });
        });
        ['dragleave', 'drop'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => {
                e.preventDefault();
                dropZone.classList.remove('dragover');
            });
        });
        dropZone.addEventListener('drop', (e) => {
            const files = e.dataTransfer.files;
            if (files.length > 0) uploadFiles(files);
        });
    }

    // Direct upload: file input
    const fileInput = document.getElementById('file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) uploadFiles(e.target.files);
            e.target.value = '';
        });
    }
});

// ─── Step Navigation ─────────────────────────────────────────────

function goToStep(step) {
    if (step === 2 && !state.connected) return;
    if (step === 3 && !state.validationResult?.valid) return;

    document.querySelectorAll('.step-content').forEach(el => el.classList.remove('active'));
    document.getElementById(`step-${step}`).classList.add('active');

    document.querySelectorAll('.steps .step').forEach(el => {
        const s = parseInt(el.dataset.step);
        el.classList.remove('active', 'completed');
        if (s === step) el.classList.add('active');
        else if (s < step) el.classList.add('completed');
    });

    state.currentStep = step;

    if (step === 2) {
        updateSourceUI();
        if (state.sourceType !== 'direct') refreshFiles();
        updateSourceOptions();
    }
    if (step === 3) {
        renderValidationResults();
        updateSourceOptions();
    }
}

function updateSourceOptions() {
    const st = state.sourceType;
    document.getElementById('google-options').classList.toggle('hidden', st !== 'google-photos');
    document.getElementById('icloud-options').classList.toggle('hidden', st !== 'icloud');
    document.getElementById('direct-options').classList.toggle('hidden', st !== 'direct');
}

function updateSourceUI() {
    const isDirect = state.sourceType === 'direct';
    document.getElementById('file-browser-section').classList.toggle('hidden', isDirect);
    document.getElementById('direct-upload-section').classList.toggle('hidden', !isDirect);
    if (isDirect) {
        renderUploadedFiles();
    }
}

// ─── Step 1: Connection ──────────────────────────────────────────

function toggleApiKey() {
    const input = document.getElementById('api-key');
    const btn = input.parentElement.querySelector('.toggle-visibility');
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = 'Hide';
    } else {
        input.type = 'password';
        btn.textContent = 'Show';
    }
}

async function testConnection() {
    const url = document.getElementById('immich-url').value.trim();
    const key = document.getElementById('api-key').value.trim();
    const statusEl = document.getElementById('connection-status');

    if (!url || !key) {
        showStatus(statusEl, 'error', 'Please enter both the server URL and API key.');
        return;
    }

    showStatus(statusEl, 'warning', 'Testing connection...');

    try {
        const resp = await fetch('/api/config/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ immich_url: url, api_key: key }),
        });

        let data;
        try {
            data = await resp.json();
        } catch {
            const text = await resp.text().catch(() => '');
            state.connected = false;
            showStatus(statusEl, 'error', `Server returned invalid response (HTTP ${resp.status})`);
            console.error('PhotoBridge test response:', resp.status, text);
            document.getElementById('btn-next-1').disabled = true;
            return;
        }

        if (data.success) {
            state.connected = true;
            state.immichUrl = url;
            state.apiKey = key;
            localStorage.setItem('pb_immich_url', url);
            localStorage.setItem('pb_api_key', key);
            showStatus(statusEl, 'success', `Connected! Logged in as ${data.user} (${data.email})`);
            document.getElementById('btn-next-1').disabled = false;
        } else {
            state.connected = false;
            showStatus(statusEl, 'error', data.error || 'Connection failed');
            document.getElementById('btn-next-1').disabled = true;
        }
    } catch (e) {
        state.connected = false;
        console.error('PhotoBridge API error:', e);
        showStatus(statusEl, 'error', `Cannot reach PhotoBridge server: ${e.message}`);
        document.getElementById('btn-next-1').disabled = true;
    }
}

// ─── Step 2: File Selection ──────────────────────────────────────

async function refreshFiles() {
    const listEl = document.getElementById('file-list');
    listEl.innerHTML = '<div class="loading">Loading files...</div>';

    try {
        const resp = await fetch('/api/files');
        const data = await resp.json();

        if (!data.files || data.files.length === 0) {
            listEl.innerHTML = '<div class="empty">No files found. Place your Takeout ZIP files in the /import volume and click Refresh.</div>';
            return;
        }

        listEl.innerHTML = '';
        data.files.forEach(file => {
            const item = document.createElement('div');
            item.className = 'file-item';
            if (state.selectedFiles.includes(file.name)) {
                item.classList.add('selected');
            }

            const icon = file.type === 'directory' ? '📁' : '📦';
            item.innerHTML = `
                <input type="checkbox" ${state.selectedFiles.includes(file.name) ? 'checked' : ''}>
                <span class="file-icon">${icon}</span>
                <span class="file-name">${escapeHtml(file.name)}</span>
                <span class="file-size">${file.size_human}</span>
            `;

            item.addEventListener('click', (e) => {
                if (e.target.tagName === 'INPUT') return;
                const cb = item.querySelector('input[type="checkbox"]');
                cb.checked = !cb.checked;
                toggleFile(file.name, cb.checked, item);
            });

            item.querySelector('input[type="checkbox"]').addEventListener('change', (e) => {
                toggleFile(file.name, e.target.checked, item);
            });

            listEl.appendChild(item);
        });
    } catch (e) {
        listEl.innerHTML = '<div class="empty">Error loading files.</div>';
    }
}

function toggleFile(name, selected, itemEl) {
    if (selected && !state.selectedFiles.includes(name)) {
        state.selectedFiles.push(name);
        itemEl.classList.add('selected');
    } else if (!selected) {
        state.selectedFiles = state.selectedFiles.filter(f => f !== name);
        itemEl.classList.remove('selected');
    }
    updateValidateButton();
    // Reset validation when files change
    state.validationResult = null;
    document.getElementById('btn-next-2').disabled = true;
    hideStatus(document.getElementById('validation-status'));
}

function updateFileSelection() {
    document.querySelectorAll('.file-item').forEach(item => {
        const name = item.querySelector('.file-name').textContent;
        const cb = item.querySelector('input[type="checkbox"]');
        const isSelected = state.selectedFiles.includes(name);
        cb.checked = isSelected;
        item.classList.toggle('selected', isSelected);
    });
}

function updateValidateButton() {
    if (state.sourceType === 'direct') {
        document.getElementById('btn-validate').disabled = state.uploadedFiles.length === 0 || state.uploading;
    } else {
        document.getElementById('btn-validate').disabled = state.selectedFiles.length === 0;
    }
}

async function validateFiles() {
    const statusEl = document.getElementById('validation-status');
    showStatus(statusEl, 'warning', 'Validating files...');

    try {
        const filesToValidate = state.sourceType === 'direct'
            ? [state.uploadDir]
            : state.selectedFiles;
        const resp = await fetch('/api/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_type: state.sourceType,
                files: filesToValidate,
            }),
        });
        const result = await resp.json();
        state.validationResult = result;

        if (result.valid) {
            const mediaCount = result.media_count || 0;
            showStatus(statusEl, 'success',
                `Valid! Found ${mediaCount} media files (${result.total_size_human}).`);
            document.getElementById('btn-next-2').disabled = false;
        } else {
            const errors = result.errors?.join('; ') || 'Validation failed';
            showStatus(statusEl, 'error', errors);
            document.getElementById('btn-next-2').disabled = true;
        }
    } catch (e) {
        showStatus(statusEl, 'error', 'Validation request failed.');
        document.getElementById('btn-next-2').disabled = true;
    }
}

// ─── Step 3: Configure & Start ───────────────────────────────────

function renderValidationResults() {
    const container = document.getElementById('validation-results');
    const result = state.validationResult;
    if (!result) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    let html = '<h3>Validation Summary</h3>';

    html += `
        <div class="validation-stat">
            <span class="label">Media files</span>
            <span class="value">${result.media_count || 0}</span>
        </div>
        <div class="validation-stat">
            <span class="label">Total size</span>
            <span class="value">${result.total_size_human || '-'}</span>
        </div>
    `;

    if (result.json_count !== undefined) {
        html += `
            <div class="validation-stat">
                <span class="label">Metadata files</span>
                <span class="value">${result.json_count}</span>
            </div>
        `;
    }

    if (result.albums && result.albums.length > 0) {
        html += `
            <div class="validation-albums">
                <span class="label">Albums found (${result.albums.length}):</span>
                <div class="album-list">
                    ${result.albums.map(a => `<span class="album-tag">${escapeHtml(a)}</span>`).join('')}
                </div>
            </div>
        `;
    }

    if (result.warnings && result.warnings.length > 0) {
        html += '<div class="validation-warnings">';
        result.warnings.forEach(w => {
            html += `<div class="validation-warning">${escapeHtml(w)}</div>`;
        });
        html += '</div>';
    }

    container.innerHTML = html;
}

async function startImport() {
    const dryRun = document.getElementById('opt-dry-run').checked;
    const options = {};

    if (state.sourceType === 'google-photos') {
        options.sync_albums = document.getElementById('opt-sync-albums').checked;
        options.include_archived = document.getElementById('opt-include-archived').checked;
        options.include_partner = document.getElementById('opt-include-partner').checked;
        options.include_trashed = document.getElementById('opt-include-trashed').checked;
        options.include_unmatched = document.getElementById('opt-include-unmatched').checked;
    } else if (state.sourceType === 'icloud') {
        options.memories = document.getElementById('opt-memories').checked;
    } else if (state.sourceType === 'direct') {
        const folderAlbum = document.getElementById('opt-folder-album').checked;
        options.folder_as_album = folderAlbum
            ? document.getElementById('opt-album-mode').value
            : 'NONE';
    }

    const dateRange = document.getElementById('opt-date-range').value.trim();
    if (dateRange) options.date_range = dateRange;

    const importFiles = state.sourceType === 'direct'
        ? [state.uploadDir]
        : state.selectedFiles;

    try {
        const resp = await fetch('/api/import/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                immich_url: state.immichUrl,
                api_key: state.apiKey,
                source_type: state.sourceType,
                files: importFiles,
                dry_run: dryRun,
                options: options,
            }),
        });
        const data = await resp.json();

        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }

        state.jobId = data.job_id;
        goToStep(4);
        startLogStream(data.job_id);
        startTimer();

    } catch (e) {
        alert('Failed to start import: ' + e.message);
    }
}

// ─── Step 4: Import Progress ─────────────────────────────────────

function startLogStream(jobId) {
    const logEl = document.getElementById('log-output');
    logEl.innerHTML = '';

    const badge = document.getElementById('import-status-badge');
    badge.className = 'badge badge-running';
    badge.textContent = 'Running';

    document.getElementById('btn-cancel').classList.remove('hidden');
    document.getElementById('btn-new-import').classList.add('hidden');

    const eventSource = new EventSource(`/api/import/${jobId}/stream`);
    state.eventSource = eventSource;

    eventSource.addEventListener('log', (e) => {
        const data = JSON.parse(e.data);
        const line = document.createElement('div');
        line.className = 'log-line';

        if (data.line.startsWith('[PhotoBridge]')) {
            line.classList.add('photobridge');
        } else if (data.line.toLowerCase().includes('error')) {
            line.classList.add('error-line');
        }

        line.textContent = data.line;
        logEl.appendChild(line);
        logEl.scrollTop = logEl.scrollHeight;
    });

    eventSource.addEventListener('done', (e) => {
        const data = JSON.parse(e.data);
        eventSource.close();
        state.eventSource = null;
        stopTimer();

        badge.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
        badge.className = `badge badge-${data.status}`;

        if (data.duration) {
            document.getElementById('import-timer').textContent = `Duration: ${data.duration}`;
        }

        document.getElementById('btn-cancel').classList.add('hidden');
        document.getElementById('btn-new-import').classList.remove('hidden');
    });

    eventSource.addEventListener('error', () => {
        if (eventSource.readyState === EventSource.CLOSED) {
            stopTimer();
            document.getElementById('btn-cancel').classList.add('hidden');
            document.getElementById('btn-new-import').classList.remove('hidden');
        }
    });
}

async function cancelImport() {
    if (!state.jobId) return;

    try {
        await fetch(`/api/import/${state.jobId}/cancel`, { method: 'POST' });
    } catch (e) {
        // Will be reflected in SSE stream
    }
}

function startTimer() {
    state.importStartTime = Date.now();
    const timerEl = document.getElementById('import-timer');

    state.timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - state.importStartTime) / 1000);
        const min = Math.floor(elapsed / 60);
        const sec = elapsed % 60;
        timerEl.textContent = `Elapsed: ${min}m ${sec.toString().padStart(2, '0')}s`;
    }, 1000);
}

function stopTimer() {
    if (state.timerInterval) {
        clearInterval(state.timerInterval);
        state.timerInterval = null;
    }
}

function newImport() {
    state.jobId = null;
    state.validationResult = null;
    state.selectedFiles = [];
    state.uploadedFiles = [];
    state.uploadDir = null;
    state.uploading = false;
    goToStep(1);
}

// ─── Direct Upload ──────────────────────────────────────────────

const CHUNK_SIZE = 30 * 1024 * 1024; // 30MB chunks

async function uploadFiles(fileList) {
    state.uploading = true;
    updateValidateButton();

    const progressEl = document.getElementById('upload-progress');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    progressEl.classList.remove('hidden');
    progressFill.style.width = '0%';

    let totalSize = 0;
    let uploadedSize = 0;
    for (let i = 0; i < fileList.length; i++) totalSize += fileList[i].size;

    try {
        for (let i = 0; i < fileList.length; i++) {
            const file = fileList[i];

            if (file.size > CHUNK_SIZE) {
                // Chunked upload for large files
                const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
                for (let c = 0; c < totalChunks; c++) {
                    if (state.uploadPaused) await waitForResume();

                    const start = c * CHUNK_SIZE;
                    const end = Math.min(start + CHUNK_SIZE, file.size);
                    const blob = file.slice(start, end);

                    const fd = new FormData();
                    fd.append('chunk', blob);
                    fd.append('filename', file.name);
                    fd.append('chunk_index', c);
                    fd.append('total_chunks', totalChunks);
                    if (state.uploadDir) fd.append('upload_id', state.uploadDir);

                    const result = await xhrUpload('/api/upload/chunk', fd, (loaded) => {
                        const pct = Math.round(((uploadedSize + start + loaded) / totalSize) * 100);
                        progressFill.style.width = pct + '%';
                        progressText.textContent = `Uploading ${file.name} (${c + 1}/${totalChunks})... ${pct}%`;
                    });

                    if (result.aborted) { c--; continue; }
                    if (result.error) throw new Error(result.error);
                    if (result.upload_dir) state.uploadDir = result.upload_dir;
                    if (result.complete && result.file) {
                        state.uploadedFiles.push(result.file);
                    }
                }
            } else {
                if (state.uploadPaused) await waitForResume();

                // Single request for small files
                const fd = new FormData();
                fd.append('files', file);
                if (state.uploadDir) fd.append('upload_id', state.uploadDir);

                const result = await xhrUpload('/api/upload', fd, (loaded) => {
                    const pct = Math.round(((uploadedSize + loaded) / totalSize) * 100);
                    progressFill.style.width = pct + '%';
                    progressText.textContent = `Uploading ${file.name} (${i + 1}/${fileList.length})... ${pct}%`;
                });

                if (result.aborted) { i--; continue; }
                if (result.error) throw new Error(result.error);
                if (result.upload_dir) state.uploadDir = result.upload_dir;
                if (result.files) state.uploadedFiles = state.uploadedFiles.concat(result.files);
            }

            uploadedSize += file.size;
        }

        progressText.textContent = `Uploaded ${fileList.length} file(s) successfully`;
        document.getElementById('btn-pause').classList.add('hidden');
        setTimeout(() => progressEl.classList.add('hidden'), 2000);

        renderUploadedFiles();
        state.uploading = false;
        state.uploadPaused = false;
        state.validationResult = null;
        document.getElementById('btn-next-2').disabled = true;
        hideStatus(document.getElementById('validation-status'));
        updateValidateButton();

    } catch (e) {
        progressText.textContent = 'Upload error: ' + e.message;
        state.uploading = false;
        state.uploadPaused = false;
        updateValidateButton();
    }
}

function toggleUploadPause() {
    const btn = document.getElementById('btn-pause');
    const progressText = document.getElementById('progress-text');
    if (state.uploadPaused) {
        state.uploadPaused = false;
        btn.textContent = 'Pause';
        btn.classList.remove('btn-resume');
        progressText.textContent = 'Resuming...';
    } else {
        state.uploadPaused = true;
        btn.textContent = 'Resume';
        btn.classList.add('btn-resume');
        progressText.textContent = 'Paused';
        if (state.currentXhr) {
            state.currentXhr.abort();
        }
    }
}

function waitForResume() {
    return new Promise((resolve) => {
        const check = () => {
            if (!state.uploadPaused) resolve();
            else setTimeout(check, 200);
        };
        check();
    });
}

function xhrUpload(url, formData, onProgress) {
    return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        state.currentXhr = xhr;
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) onProgress(e.loaded);
        });
        xhr.addEventListener('load', () => {
            state.currentXhr = null;
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve(JSON.parse(xhr.responseText));
            } else {
                resolve({ error: `Upload failed (HTTP ${xhr.status})` });
            }
        });
        xhr.addEventListener('abort', () => {
            state.currentXhr = null;
            resolve({ aborted: true });
        });
        xhr.addEventListener('error', () => {
            state.currentXhr = null;
            resolve({ error: 'Network error' });
        });
        xhr.open('POST', url);
        xhr.send(formData);
    });
}

async function removeUploadedFile(filename) {
    if (!state.uploadDir) return;
    try {
        const resp = await fetch('/api/upload/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upload_id: state.uploadDir, filename }),
        });
        const data = await resp.json();
        if (data.success) {
            state.uploadedFiles = state.uploadedFiles.filter(f => f.name !== filename);
            state.validationResult = null;
            document.getElementById('btn-next-2').disabled = true;
            hideStatus(document.getElementById('validation-status'));
            renderUploadedFiles();
            updateValidateButton();
        }
    } catch (e) {
        // ignore
    }
}

function renderUploadedFiles() {
    const container = document.getElementById('uploaded-files');
    if (state.uploadedFiles.length === 0) {
        container.innerHTML = '';
        return;
    }

    let html = '<div class="uploaded-files-header"><h3>Uploaded Files</h3></div>';
    html += '<div class="file-list">';
    state.uploadedFiles.forEach((file) => {
        const icon = file.name.toLowerCase().endsWith('.zip') ? '📦' : '🖼️';
        const escapedName = escapeHtml(file.name).replace(/'/g, "\\'");
        html += `
            <div class="file-item">
                <span class="file-icon">${icon}</span>
                <span class="file-name">${escapeHtml(file.name)}</span>
                <span class="file-size">${file.size_human}</span>
                <button class="btn-remove" onclick="removeUploadedFile('${escapedName}')" title="Remove">&times;</button>
            </div>
        `;
    });
    html += '</div>';
    container.innerHTML = html;
}

// ─── Utilities ───────────────────────────────────────────────────

function showStatus(el, type, message) {
    el.className = `status-message ${type}`;
    el.textContent = message;
    el.classList.remove('hidden');
}

function hideStatus(el) {
    el.classList.add('hidden');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
