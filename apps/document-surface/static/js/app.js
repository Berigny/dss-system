/**
 * Document surface frontend helpers.
 * Handles the settings panel and HTMX error toasts.
 */

function toggleMenu() {
    const panel = document.getElementById('settings-panel');
    const overlay = document.getElementById('menu-overlay');
    const icon = document.getElementById('hamburger-icon');
    if (!panel) return;
    const isOpen = panel.classList.contains('open');
    if (isOpen) {
        panel.classList.remove('open');
        overlay && overlay.classList.remove('open');
        icon && icon.classList.remove('open');
    } else {
        panel.classList.add('open');
        overlay && overlay.classList.add('open');
        icon && icon.classList.add('open');
    }
}

function showToast(message, type = 'error') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

document.addEventListener('htmx:responseError', function (event) {
    const xhr = event.detail?.xhr;
    if (!xhr) return;
    let message = `Request failed (${xhr.status})`;
    try {
        const data = JSON.parse(xhr.responseText);
        if (data.detail) message = data.detail;
        else if (data.error) message = data.error;
    } catch (_e) {
        // ignore parse error
    }
    showToast(message, 'error');
});

document.addEventListener('htmx:afterRequest', function (event) {
    if (event.detail?.successful === false) return;
    const target = event.detail?.target;
    if (target && target.classList.contains('error-message')) {
        target.remove();
    }
});
