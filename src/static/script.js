function initThemeButton() {
    const root = document.documentElement;
    const button = document.getElementById('themeToggle');
    const next = { system: 'dark', dark: 'light', light: 'system' };
    const update = () => {
        const theme = root.dataset.theme;
        const label = `Theme: ${theme}. Switch to ${next[theme]} theme`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.classList.toggle('theme-system', theme === 'system');
    };
    update();
    button.addEventListener('click', () => {
        const theme = next[root.dataset.theme];
        root.dataset.theme = theme;
        const age = theme === 'system' ? 0 : 31536000;
        document.cookie = `theme=${theme}; max-age=${age}; path=/; SameSite=Lax`;
        update();
    });
}

let lastSnapshotRevision = -1;
const byId = id => document.getElementById(id);

function applySnapshot(data) {
    if (!data) return;
    const revision = typeof data.revision === 'number' ? data.revision : 0;
    if (revision < lastSnapshotRevision) return;
    lastSnapshotRevision = revision;
    updateServerStatusUI(data.state);
    updatePlayerInfoUI(data);
    byId('palServerVersion').textContent = data.palServerVersion || '—';
    byId('uptime').textContent = formatUptime(data.uptimeSeconds);
    byId('lastUpdatedText').textContent = new Date().toLocaleTimeString();
}

function formatUptime(seconds) {
    if (!Number.isInteger(seconds) || seconds < 0) return '—';
    const parts = [];
    for (const [size, label] of [[86400, 'd'], [3600, 'h'], [60, 'm'], [1, 's']]) {
        const value = Math.floor(seconds / size);
        if (value) parts.push(`${value}${label}`);
        seconds %= size;
    }
    return parts.join(' ') || '0s';
}

function setVisible(id, visible) {
    const element = byId(id);
    if (element) element.style.display = visible ? '' : 'none';
}

function updateServerStatusUI(state) {
    setVisible('serverStatusOn', state === 'RUNNING');
    setVisible('serverStatusOff', state === 'STOPPED');
    byId('serverStateText').textContent = state === 'STARTING' ? 'Starting' : 'Stopping';
    setVisible('serverStateText', state === 'STARTING' || state === 'STOPPING');
    setVisible('autoStopNotice', state === 'RUNNING');
    setVisible('offBtn', state === 'RUNNING');
    setVisible('onBtn', state === 'STOPPED');
}

function updatePlayerInfoUI(data) {
    const roster = byId('playersInfo');
    const playerData = data.playerData || 'unknown';
    const stopped = data.state === 'STOPPED';
    byId('onlineCount').textContent = stopped ? '0 online' : playerData === 'ok' && Number.isInteger(data.playerCount)
        ? `${data.playerCount} online` : '— online';
    byId('playerDataStatus').textContent = playerData === 'unavailable'
        ? 'Player data unavailable - showing last known roster' : 'Player data not available yet';
    setVisible('playerDataStatus', !stopped && playerData !== 'ok');

    const banned = new Set(data.banned_players || []);
    const players = (data.players || []).map(player => ({ ...player, banned: banned.has(player.steam_id) }));
    const known = new Set(players.map(player => player.steam_id));
    for (const id of banned) {
        if (!known.has(id)) players.push({ steam_id: id, name: id, level: 'N/A', currently_online: false, banned: true });
    }
    players.sort((a, b) =>
        Number(b.currently_online) - Number(a.currently_online) ||
        Number(a.banned) - Number(b.banned) ||
        (parseInt(b.level, 10) || 0) - (parseInt(a.level, 10) || 0) ||
        a.name.localeCompare(b.name));

    // Preserve the focused control across roster refreshes.
    const focused = roster.contains(document.activeElement) ? document.activeElement : null;
    let replacementFocus;
    if (players.length) {
        const table = document.createElement('table');
        table.className = 'players-table';
        table.setAttribute('role', 'table');
        table.setAttribute('aria-labelledby', 'rosterTitle');
        table.innerHTML = `<thead role="rowgroup"><tr role="row">
            <th scope="col" role="columnheader">Player</th><th scope="col" role="columnheader">Level</th>
            <th scope="col" role="columnheader">Status</th><th scope="col" role="columnheader">Actions</th>
        </tr></thead><tbody role="rowgroup"></tbody>`;

        for (const player of players) {
            const row = table.tBodies[0].insertRow();
            const status = player.banned ? 'Banned' : player.currently_online ? 'Online' : 'Offline';
            const state = status.toLowerCase();
            row.className = `player-row ${state}`;
            row.setAttribute('role', 'row');
            row.innerHTML = `<td class="player-name" role="cell"><div class="player-identity"><span class="player-avatar" aria-hidden="true"></span><span></span></div></td>
                <td class="player-level" role="cell"></td>
                <td class="player-status" role="cell"><span class="status-pill status-${state}">${status}</span></td>
                <td class="player-actions" role="cell"><div></div></td>`;
            const [identity, level, statusCell, actions] = row.cells;
            identity.querySelector('.player-avatar').textContent = Array.from(player.name)[0]?.toUpperCase() || '?';
            identity.querySelector('.player-identity > :last-child').textContent = player.name;
            level.textContent = player.level === 'N/A' ? '—' : `LVL ${player.level}`;
            if (state === 'offline' && player.last_online) {
                const seen = document.createElement('small');
                seen.className = 'last-seen';
                seen.textContent = formatTimestamp(player.last_online);
                statusCell.append(seen);
            }
            const allowed = player.banned ? ['Unban'] : player.currently_online ? ['Kick', 'Ban'] : ['Ban'];
            for (const action of allowed) {
                const button = playerActionButton(action, player);
                actions.firstElementChild.append(button);
                if (focused?.dataset.player === button.dataset.player && focused.textContent === action) {
                    replacementFocus = button;
                }
            }
        }
        roster.replaceChildren(table);
    } else {
        const empty = document.createElement('p');
        empty.className = 'empty-roster';
        empty.textContent = stopped || playerData === 'ok' ? 'No players found' :
            playerData === 'unavailable' ? 'Player data unavailable' : 'Waiting for player data…';
        roster.replaceChildren(empty);
    }
    if (focused) (replacementFocus || roster).focus({ preventScroll: true });
}

function formatTimestamp(timestamp) {
    const seconds = Date.now() / 1000 - timestamp;
    if (!Number.isFinite(seconds)) return 'Unknown';
    if (seconds < 60) return 'Just now';
    const [unit, duration] = seconds < 3600 ? ['minute', 60] : seconds < 86400 ? ['hour', 3600] : ['day', 86400];
    const count = Math.floor(seconds / duration);
    return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
}

function playerActionButton(action, player) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `${action.toLowerCase()}-btn`;
    button.textContent = action;
    button.dataset.player = player.steam_id;
    button.setAttribute('aria-label', `${action} ${player.name}`);
    button.addEventListener('click', () => handlePlayerAction(action, player));
    return button;
}

async function postAction(path, fields) {
    const formData = new FormData();
    for (const [key, value] of Object.entries(fields)) formData.append(key, value);
    formData.append('csrf_token', document.querySelector('meta[name="csrf-token"]').content);
    const response = await fetch(path, { method: 'POST', body: formData });
    if (response.status === 401) {
        window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
        return null;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

async function handleServerAction(action) {
    const feedback = byId('actionFeedback');
    feedback.textContent = '';
    try {
        const result = await postAction('/action', { action });
        if (result) applySnapshot(result.data);
    } catch (error) {
        console.error('Server request failed:', error);
        feedback.textContent = 'Could not complete the server request. Try again.';
    }
}

function confirmAndHandleServerAction(action) {
    if (confirm('Are you sure you want to stop the server?')) handleServerAction(action);
}

async function handlePlayerAction(action, player) {
    const verb = action.toLowerCase();
    const consequence = action === 'Ban' ? '\n\nThis will kick them immediately and prevent them from rejoining.' : '';
    if (!confirm(`Are you sure you want to ${verb} ${player.name}?${consequence}`)) return;
    const feedback = byId('moderationFeedback');
    feedback.textContent = '';
    try {
        const result = await postAction(`/${verb}`, { steam_id: player.steam_id });
        if (!result) return;
        applySnapshot(result.data);
        const completed = { Kick: 'kicked', Ban: 'banned', Unban: 'unbanned' };
        feedback.textContent = result.success ? `${player.name} was ${completed[action]}.` :
            `Failed to ${verb} ${player.name}. ${result.message || ''}`.trim();
    } catch (error) {
        console.error(`Could not ${verb} player:`, error);
        feedback.textContent = `Could not ${verb} ${player.name}. Try again.`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initThemeButton();
    const streamStatus = byId('streamStatus');
    if (!streamStatus) return;
    handleServerAction('getStatus');
    const stream = new EventSource('/stream');
    stream.onopen = () => {
        // A restarted controller can start revisions over.
        lastSnapshotRevision = -1;
    };
    stream.onmessage = event => {
        try {
            applySnapshot(JSON.parse(event.data).data);
            if (streamStatus.dataset.connection !== 'live') {
                streamStatus.dataset.connection = 'live';
                streamStatus.textContent = 'Connected';
            }
        } catch (error) {
            console.error('Could not read live update:', error);
        }
    };
    stream.onerror = () => {
        streamStatus.dataset.connection = 'pending';
        if (stream.readyState === EventSource.CLOSED) {
            streamStatus.textContent = 'Disconnected';
        } else if (streamStatus.textContent !== 'Reconnecting') {
            streamStatus.textContent = 'Reconnecting';
        }
    };
});
