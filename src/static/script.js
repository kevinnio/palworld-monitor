// Theme management
function getThemeFromCookie() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [name, value] = cookie.trim().split('=');
        if (name === 'theme') {
            return value;
        }
    }
    return null;
}

function setThemeInCookie(theme) {
    if (theme) {
        // Set cookie with 1 year expiration
        const expirationDate = new Date();
        expirationDate.setFullYear(expirationDate.getFullYear() + 1);
        document.cookie = `theme=${theme}; expires=${expirationDate.toUTCString()}; path=/`;
    } else {
        // Remove cookie by setting expiration in the past
        document.cookie = 'theme=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    }
}

function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function initThemeButton() {
    const currentTheme = document.body.getAttribute('data-theme');
    updateThemeButton(currentTheme);

    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        // Only auto-switch if no manual theme is set in cookie
        if (!getThemeFromCookie()) {
            const newTheme = e.matches ? 'dark' : 'light';
            document.body.setAttribute('data-theme', newTheme);
            updateThemeButton(newTheme);
        }
    });
}

function toggleTheme() {
    const currentTheme = document.body.getAttribute('data-theme');
    const isManualTheme = getThemeFromCookie() !== null;

    // If currently following system theme, set manual theme
    if (!isManualTheme) {
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setThemeInCookie(newTheme);
        // Reload page to apply theme from server
        window.location.reload();
    } else {
        // If manual theme is set, reset to system theme
        resetToSystemTheme();
    }
}

function resetToSystemTheme() {
    setThemeInCookie(null); // Remove manual theme from cookie
    // Reload page to apply system theme from server
    window.location.reload();
}

function updateThemeButton(theme) {
    const button = document.getElementById('themeToggle');
    const isManualTheme = getThemeFromCookie() !== null;

    if (theme === 'dark') {
        button.innerHTML = '<span class="icon">☀️</span>';
        button.title = isManualTheme ? 'Switch to light mode' : 'System dark mode (click to override)';
    } else {
        button.innerHTML = '<span class="icon">🌙</span>';
        button.title = isManualTheme ? 'Switch to dark mode' : 'System light mode (click to override)';
    }

    // Add visual indicator for system theme
    if (!isManualTheme) {
        button.classList.add('system-theme');
    } else {
        button.classList.remove('system-theme');
    }
}

// UI update functions
let lastSnapshotRevision = -1;

function applySnapshot(data) {
    if (!data) return;
    const revision = typeof data.revision === "number" ? data.revision : 0;
    if (revision < lastSnapshotRevision) return;
    lastSnapshotRevision = revision;
    updateServerStatusUI(data);
    updatePlayerInfoUI(data);
    updateLastUpdatedUI();
}

function updateServerStatusUI(data) {
    const runningElements = document.querySelectorAll(".status-indicator.running");
    const offElements = document.querySelectorAll(".status-indicator.off");
    const offBtn = document.getElementById("offBtn");
    const onBtn = document.getElementById("onBtn");
    const statusOn = document.querySelector('.status-on');
    const statusOff = document.querySelector('.status-off');
    const stateText = document.getElementById("serverStateText");
    const autoStopNotice = document.getElementById("autoStopNotice");

    const state = data.state || "STOPPED";
    // The process is only known to be alive between RUNNING and a confirmed exit.
    const processLive = state === "RUNNING" || state === "STOPPING";

    if (processLive) {
        if (statusOn) statusOn.style.display = "inline-block";
        if (statusOff) statusOff.style.display = "none";
        runningElements.forEach(el => el.style.display = "inline-block");
        offElements.forEach(el => el.style.display = "none");
    }
    else {
        if (statusOn) statusOn.style.display = "none";
        if (statusOff) statusOff.style.display = "inline-block";
        runningElements.forEach(el => el.style.display = "none");
        offElements.forEach(el => el.style.display = "inline-block");
    }

    if (stateText) {
        if (state === "STARTING" || state === "STOPPING") {
            stateText.textContent = state;
            stateText.style.display = "inline-block";
        } else {
            stateText.style.display = "none";
        }
    }

    if (autoStopNotice) {
        autoStopNotice.style.display = state === "RUNNING" ? "block" : "none";
    }

    // Only offer a command that matches the observed state.
    if (offBtn) offBtn.style.display = state === "RUNNING" ? "block" : "none";
    if (onBtn) onBtn.style.display = state === "STOPPED" ? "block" : "none";
}

function updatePlayerInfoUI(data) {
    const playersInfoElement = document.getElementById("playersInfo");
    if (!playersInfoElement) return;

    const playerData = data.playerData || "unknown";
    const stopped = data.state === "STOPPED";
    const statusElement = document.getElementById("playerDataStatus");
    if (statusElement) {
        if (stopped) {
            statusElement.style.display = "none";
        } else if (playerData === "unavailable") {
            statusElement.textContent = "Player data unavailable - showing last known roster";
            statusElement.style.display = "block";
        } else if (playerData === "unknown") {
            statusElement.textContent = "Player data not available yet";
            statusElement.style.display = "block";
        } else {
            statusElement.style.display = "none";
        }
    }

    // Get banned players list from the snapshot
    const bannedPlayers = data.banned_players || [];

    // Create a set of banned Steam IDs for quick lookup
    const bannedSteamIds = new Set(bannedPlayers);

    // Combine online/offline players with banned players
    let allPlayers = (data.players || []).map(player => ({
        ...player,
        banned: bannedSteamIds.has(player.steam_id)
    }));

    // Add banned players who are not in the current player list
    bannedPlayers.forEach(steamId => {
        const alreadyInList = allPlayers.some(player => player.steam_id === steamId);
        if (!alreadyInList) {
            // Add banned player with placeholder data
            allPlayers.push({
                steam_id: steamId,
                name: steamId, // Use Steam ID as name for banned players not currently tracked
                level: 'N/A',
                currently_online: false,
                last_online: null,
                banned: true
            });
        }
    });

    if (allPlayers.length > 0) {
        // Sort by online first, then banned status, then level (desc), then name
        const sortedPlayers = allPlayers.sort((a, b) => {
            // Online players first
            if (a.currently_online !== b.currently_online) return b.currently_online - a.currently_online;
            // Then banned players
            if ((a.banned || false) !== (b.banned || false)) return (a.banned || false) ? 1 : -1;
            // Then by level (descending)
            const levelA = parseInt(a.level) || 0;
            const levelB = parseInt(b.level) || 0;
            if (levelA !== levelB) return levelB - levelA;
            // Finally by name
            return a.name.localeCompare(b.name);
        });

        // Create table
        let tableHTML = `
            <table class="players-table">
                <thead>
                    <tr>
                        <th>Player</th>
                        <th>Level</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
        `;

        sortedPlayers.forEach(player => {
            const isBanned = player.banned || false;
            const statusText = isBanned ? 'Banned' : (player.currently_online ? 'Online' : formatTimestamp(player.last_online));
            const statusClass = isBanned ? 'status-banned' : (player.currently_online ? 'status-online' : 'status-offline');
            const rowClass = isBanned ? 'banned' : (player.currently_online ? 'online' : 'offline');

            // Show action buttons based on player status and ban status
            let actionButtons = '';
            if (isBanned) {
                actionButtons = playerActionButton('Unban', player);
            } else if (player.currently_online) {
                actionButtons = playerActionButton('Kick', player) + playerActionButton('Ban', player);
            } else {
                actionButtons = playerActionButton('Ban', player);
            }

            tableHTML += `
                <tr class="player-row ${rowClass}">
                    <td class="player-name">${escapeHtml(player.name)}</td>
                    <td class="player-level">LVL ${escapeHtml(String(player.level))}</td>
                    <td class="${statusClass}">${statusText}</td>
                    <td class="player-actions">${actionButtons}</td>
                </tr>
            `;
        });

        tableHTML += `
                </tbody>
            </table>
        `;

        playersInfoElement.innerHTML = tableHTML;
    } else {
        if (stopped || playerData === "ok") {
            playersInfoElement.textContent = "- No players found";
        } else if (playerData === "unavailable") {
            playersInfoElement.textContent = "- Player data unavailable";
        } else {
            playersInfoElement.textContent = "- Waiting for player data";
        }
    }
}

function playerActionButton(action, player) {
    const button = document.createElement('button');
    button.className = `${action.toLowerCase()}-btn`;
    button.textContent = action;
    button.setAttribute('onclick', `handle${action}Player(${JSON.stringify(player.steam_id)}, ${JSON.stringify(player.name)})`);
    return button.outerHTML;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTimestamp(timestamp) {
    if (!timestamp) return "Unknown";
    try {
        const now = Date.now() / 1000;
        const diff = now - timestamp;
        if (diff < 60) {
            return "Just now";
        } else if (diff < 3600) {
            const minutes = Math.floor(diff / 60);
            return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`;
        } else if (diff < 86400) {
            const hours = Math.floor(diff / 3600);
            return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
        } else {
            const days = Math.floor(diff / 86400);
            return `${days} day${days !== 1 ? 's' : ''} ago`;
        }
    } catch (e) {
        return "Unknown";
    }
}

function updateLastUpdatedUI() {
    const lastUpdatedElement = document.getElementById("lastUpdatedText");
    if (lastUpdatedElement) lastUpdatedElement.textContent = new Date();
}

// AJAX request handling
function getCSRFToken() {
    const tokenMeta = document.querySelector('meta[name="csrf-token"]');
    return tokenMeta ? tokenMeta.getAttribute('content') : '';
}

async function makeServerRequest(action) {
    try {
        const formData = new FormData();
        formData.append('action', action);
        formData.append('csrf_token', getCSRFToken());

        const response = await fetch('/action', {
            method: 'POST',
            body: formData
        });

        // Handle session timeout - redirect to login
        if (response.status === 401) {
            window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
            return null;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('Error making server request:', error);
        throw error;
    }
}

// Confirmation handler for server stop action
function confirmAndHandleServerAction(action) {
    if (confirm('Are you sure you want to stop the server?')) {
        handleServerAction(action);
    }
}

// Main server action handler
async function handleServerAction(action) {
    try {
        const response = await makeServerRequest(action);
        let data = response.data;

        applySnapshot(data);
    } catch (error) {
        console.error('Error handling server action:', error);
    }
}

// Kick player handler
async function handleKickPlayer(steamId, playerName) {
    if (!confirm(`Are you sure you want to kick ${playerName}?`)) {
        return;
    }

    try {
        const formData = new FormData();
        formData.append('steam_id', steamId);
        formData.append('csrf_token', getCSRFToken());

        const response = await fetch('/kick', {
            method: 'POST',
            body: formData
        });

        // Handle session timeout - redirect to login
        if (response.status === 401) {
            window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();

        if (result.success === true) {
            alert(`${playerName} has been kicked from the server.`);
        } else {
            alert(`Failed to kick ${playerName}. ${result.message || ''}`);
        }

        // Update UI with new data
        applySnapshot(result.data);
    } catch (error) {
        console.error('Error kicking player:', error);
        alert('An error occurred while trying to kick the player.');
    }
}

// Ban player handler
async function handleBanPlayer(steamId, playerName) {
    if (!confirm(`Are you sure you want to ban ${playerName}?\n\nThis will kick them immediately and prevent them from rejoining.`)) {
        return;
    }

    try {
        const formData = new FormData();
        formData.append('steam_id', steamId);
        formData.append('csrf_token', getCSRFToken());

        const response = await fetch('/ban', {
            method: 'POST',
            body: formData
        });

        // Handle session timeout - redirect to login
        if (response.status === 401) {
            window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();

        if (result.success === true) {
            alert(`${playerName} has been banned from the server.`);
        } else {
            alert(`Failed to ban ${playerName}. ${result.message || ''}`);
        }

        // Update UI with new data
        if (result.data) {
            applySnapshot(result.data);
        }
    } catch (error) {
        console.error('Error banning player:', error);
        alert('An error occurred while trying to ban the player.');
    }
}

// Unban player handler
async function handleUnbanPlayer(steamId, playerName) {
    if (!confirm(`Are you sure you want to unban ${playerName}?`)) {
        return;
    }

    try {
        const formData = new FormData();
        formData.append('steam_id', steamId);
        formData.append('csrf_token', getCSRFToken());

        const response = await fetch('/unban', {
            method: 'POST',
            body: formData
        });

        // Handle session timeout - redirect to login
        if (response.status === 401) {
            window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();

        if (result.success === true) {
            alert(`${playerName} has been unbanned from the server.`);
        } else {
            alert(`Failed to unban ${playerName}. ${result.message || ''}`);
        }

        // Update player info with new banned status
        applySnapshot(result.data);
    } catch (error) {
        console.error('Error unbanning player:', error);
        alert('An error occurred while trying to unban the player.');
    }
}

// Update banned players UI - removed as it's now merged into the main table

// Load banned players on page load - removed as it's now loaded with server status

// Event listeners
document.addEventListener('DOMContentLoaded', function () {
    initThemeButton();
    handleServerAction("getStatus");

    // Live updates via Server-Sent Events
    const eventSource = new EventSource('/stream');
    eventSource.onopen = function () {
        // A reconnected stream may come from a restarted controller whose
        // revisions start over.
        lastSnapshotRevision = -1;
    };
    eventSource.onmessage = function (event) {
        try {
            const response = JSON.parse(event.data);
            applySnapshot(response.data);
        } catch (e) {
            console.error('Error parsing SSE data:', e);
        }
    };
    eventSource.onerror = function () {
        console.warn('SSE connection lost, will auto-reconnect…');
    };
});
