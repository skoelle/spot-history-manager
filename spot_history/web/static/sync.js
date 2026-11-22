// Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
// Licensed under the MIT License. See LICENSE file in project root for details.
(() => {
    const panel = document.getElementById("sync-status");
    if (!panel) {
        return;
    }

    const statusBody = document.getElementById("sync-status-body");
    const logElement = document.getElementById("sync-log");
    const stopButton = document.getElementById("sync-stop");
    const buttons = Array.from(document.querySelectorAll("[data-sync-action]"));
    const statusUrl = panel.dataset.statusUrl;
    const startUrl = panel.dataset.startUrl;
    const stopUrl = panel.dataset.stopUrl;
    const states = {
        queued: "Wartet",
        running: "Läuft",
        success: "Erfolgreich",
        error: "Fehler",
        cancelled: "Abgebrochen",
    };
    let pollTimer = null;

    const escapeHtml = (value) =>
        String(value == null ? "" : value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");

    const formatElapsed = (seconds) => {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        if (total < 60) {
            return `${total} s`;
        }
        const minutes = Math.floor(total / 60);
        const rest = total % 60;
        return `${minutes} min ${rest} s`;
    };

    const setBusy = (active) => {
        buttons.forEach((button) => {
            button.disabled = active;
        });
        stopButton.hidden = !active;
    };

    const scheduleRefresh = () => {
        if (pollTimer) {
            clearTimeout(pollTimer);
        }
        pollTimer = setTimeout(refresh, 2000);
    };

    const render = (data) => {
        const job = data.job;
        if (!job) {
            setBusy(false);
            statusBody.innerHTML = '<span class="sync-state">Kein Job aktiv.</span>';
            logElement.hidden = true;
            logElement.textContent = "";
            return;
        }
        setBusy(Boolean(data.active));
        const state = states[job.status] || job.status;
        const parts = [
            `<span class="sync-state ${escapeHtml(job.status)}">${escapeHtml(state)}</span>`,
            `<strong>${escapeHtml(job.label)}</strong>`,
            `<span>Start: ${escapeHtml(new Date(job.started_at).toLocaleString())}</span>`,
            `<span>Dauer: ${escapeHtml(formatElapsed(job.elapsed_s))}</span>`,
        ];
        if (job.error) {
            parts.push(`<span class="sync-state error">${escapeHtml(job.error)}</span>`);
        }
        if (job.steps && job.steps.length) {
            const steps = job.steps
                .map((step, index) => {
                    const code =
                        step.returncode == null ? "" : ` → exit ${escapeHtml(step.returncode)}`;
                    return `<div class="sync-step">Schritt ${index + 1}: <code>${escapeHtml(
                        step.command.join(" "),
                    )}</code>${code}</div>`;
                })
                .join("");
            parts.push(`<div class="sync-steps">${steps}</div>`);
        }
        statusBody.innerHTML = parts.join(" ");
        if (job.log_tail && job.log_tail.length) {
            logElement.hidden = false;
            logElement.textContent = job.log_tail.join("\n");
            logElement.scrollTop = logElement.scrollHeight;
        } else {
            logElement.hidden = true;
            logElement.textContent = "";
        }
    };

    const showError = (message) => {
        setBusy(false);
        statusBody.innerHTML = `<span class="sync-state error">${escapeHtml(message)}</span>`;
    };

    async function refresh() {
        try {
            const response = await fetch(statusUrl, { cache: "no-store" });
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            render(data);
            if (data.active) {
                scheduleRefresh();
            }
        } catch (error) {
            showError(`Status konnte nicht geladen werden: ${error}`);
        }
    }

    const startAction = async (action) => {
        setBusy(true);
        try {
            const response = await fetch(startUrl + encodeURIComponent(action), {
                method: "POST",
                headers: { Accept: "application/json" },
            });
            if (!response.ok) {
                let message = `Start fehlgeschlagen (HTTP ${response.status}).`;
                try {
                    const data = await response.json();
                    if (data.detail) {
                        message = data.detail;
                    }
                } catch (error) {
                    message = `Start fehlgeschlagen (HTTP ${response.status}).`;
                }
                showError(message);
                return;
            }
            const data = await response.json();
            render(data);
            if (data.active) {
                scheduleRefresh();
            }
        } catch (error) {
            showError(`Start fehlgeschlagen: ${error}`);
        }
    };

    buttons.forEach((button) => {
        button.addEventListener("click", () => {
            const action = button.dataset.syncAction;
            if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
                return;
            }
            startAction(action);
        });
    });

    if (stopButton) {
        stopButton.addEventListener("click", async () => {
            stopButton.disabled = true;
            try {
                await fetch(stopUrl, {
                    method: "POST",
                    headers: { Accept: "application/json" },
                });
            } catch (error) {
                showError(`Abbrechen fehlgeschlagen: ${error}`);
                stopButton.disabled = false;
                return;
            }
            await refresh();
            stopButton.disabled = false;
        });
    }

    refresh();
})();
