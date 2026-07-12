/**
 * Attachment UI helpers extracted for reuse.
 * Exported as a small factory so callers can hook event handlers into their UI.
 */
export function createAttachmentHandlers() {
    const ATTACH_MAX_BYTES = Number.isFinite(Number(window?.dsAttachmentMaxBytes))
        ? Number(window.dsAttachmentMaxBytes)
        : 50 * 1024 * 1024;
    let attachController = null;

    function handleAttachClick() {
        document.getElementById('attach-file-input')?.click();
    }

    function handleAttachFile(event) {
        const file = event.target?.files?.[0];
        if (!file) return;
        if (file.size > ATTACH_MAX_BYTES) {
            const maxMb = Math.floor(ATTACH_MAX_BYTES / (1024 * 1024));
            alert(`File too large (max ${maxMb} MB).`);
            event.target.value = '';
            return;
        }
        showAttachPanel(file.name);
        startSimulatedIngest();
    }

    function showAttachPanel(fileName) {
        const panel = document.getElementById('attach-panel');
        const nameEl = document.getElementById('attach-file-name');
        const progress = document.getElementById('attach-progress');
        if (panel) panel.style.display = 'block';
        if (nameEl) nameEl.textContent = fileName || '';
        if (progress) progress.style.width = '0%';
    }

    function resetAttachPanel() {
        const panel = document.getElementById('attach-panel');
        const input = document.getElementById('attach-file-input');
        if (attachController?.abort) attachController.abort();
        attachController = null;
        if (panel) panel.style.display = 'none';
        if (input) input.value = '';
    }

    function startSimulatedIngest() {
        const progress = document.getElementById('attach-progress');
        if (!progress) return;
        let current = 0;
        if (attachController?.abort) attachController.abort();
        const timer = setInterval(() => {
            current = Math.min(current + 8, 100);
            progress.style.width = `${current}%`;
            if (current >= 100) {
                clearInterval(timer);
                attachController = null;
                setTimeout(resetAttachPanel, 600);
            }
        }, 120);
        attachController = { abort: () => clearInterval(timer) };
    }

    function cancelAttach(event) {
        if (event) event.stopPropagation();
        resetAttachPanel();
    }

    return {
        handleAttachClick,
        handleAttachFile,
        cancelAttach,
        resetAttachPanel,
    };
}

export default createAttachmentHandlers;
