// ════════════════════════════════════════════════════════════════
// ÉTAT
// ════════════════════════════════════════════════════════════════
const state = {
  soundsEnabled: true,
  volume:        0.8,
  currentPage:   0,
  phase:         'idle',
  triggerRigs:   {},
  eclipse:       null,
  triggerCircumstances: null,
  triggerDiamondDurationS: null,
  gps:           {},
  audioCtx:      null,
  audioBuffers:  {},
  currentSound:  null,
};

const DEVICE_CATEGORIES = ['camera', 'gps', 'focuser', 'mount'];
const DEVICE_LABELS = { camera: 'Camera', gps: 'GPS', focuser: 'Focuser', mount: 'Mount' };
const DEVICE_PLUGIN_OPTIONS = {
  camera: ['sony', 'nikon-z', 'nikon-dslr'],
  gps: ['serial_nmea', 'gpsd'],
  focuser: ['zwo_eaf'],
  mount: ['indi', 'onstep'],
};
const DEFAULT_RIGS = Array.from({length: 4}, (_, index) => ({
  rig_id: index + 1, name: `RIG ${index + 1}`, enabled: false
}));
const RIG_DEVICE_CATEGORIES = ['camera', 'mount', 'focuser'];
let rigDevicesState = {rigs: DEFAULT_RIGS, inventory: {camera: [], mount: [], focuser: []}};
const DEVICE_AUTO_REFRESH_INTERVAL_MS = 1000;
let deviceAutoRefreshInFlight = false;
let rigPhotoState = {rigs: []};
let globalDevicesState = null;

function populateRigIsoMaxSelect(select, values, requestedValue) {
  if (!select) return;

  const isoValues = Array.isArray(values)
    ? values.map(Number).filter(value => Number.isInteger(value) && value > 0)
    : [];

  select.replaceChildren();

  if (!isoValues.length) {
    const option = document.createElement('option');
    option.value = String(requestedValue || 6400);
    option.textContent = `${option.value} — profile unavailable`;
    select.appendChild(option);
    select.disabled = true;
    return;
  }

  isoValues.forEach(value => {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = String(value);
    select.appendChild(option);
  });

  const requested = Number(requestedValue);
  const selected = isoValues.includes(requested)
    ? requested
    : isoValues.filter(value => value <= requested).at(-1) || isoValues[0];

  select.value = String(selected);
  select.disabled = false;
}

function renderRigPhotoConfig(payload) {
  const rigs = Array.isArray(payload && payload.rigs) ? payload.rigs : [];
  rigPhotoState = {rigs};

  rigs.forEach(rig => {
    const rigId = Number(rig.rig_id);
    if (!Number.isInteger(rigId) || rigId < 1 || rigId > 4) return;

    const photo = rig.photo || {};
    const capabilities = rig.camera_capabilities || {};

    const antiBlur = document.getElementById(`rig-${rigId}-antiblur-switch`);
    const tolerance = document.getElementById(`rig-${rigId}-pixel-tolerance`);
    const mechanical = document.getElementById(`rig-${rigId}-mechanical-vibration-switch`);
    const mechanicalDelay = document.getElementById(`rig-${rigId}-mechanical-vibration-delay`);
    const mechanicalNote = document.getElementById(`rig-${rigId}-mechanical-vibration-note`);
    const mechanicalSection = mechanical
      ? mechanical.closest('.camcfg-mechanical-vibration-section')
      : null;
    const mechanicalAvailable = capabilities.strategy === 'sequential';
    const isoComp = document.getElementById(`rig-${rigId}-iso-comp-switch`);
    const isoMax = document.getElementById(`rig-${rigId}-iso-max`);

    if (antiBlur) antiBlur.checked = photo.anti_trailing_enabled === true;

    if (tolerance) {
      tolerance.value = photo.motion_tolerance_px == null
        ? '1.0'
        : String(photo.motion_tolerance_px);
    }

    if (mechanicalSection) {
      mechanicalSection.classList.toggle(
        'camcfg-subsection-unavailable',
        !mechanicalAvailable
      );
      mechanicalSection.setAttribute(
        'aria-disabled',
        mechanicalAvailable ? 'false' : 'true'
      );
    }

    if (mechanical) {
      mechanical.checked = mechanicalAvailable
        ? photo.mechanical_vibration_enabled === true
        : false;
      mechanical.disabled = !mechanicalAvailable;
    }

    if (mechanicalDelay) {
      mechanicalDelay.value = String(
        photo.mechanical_vibration_delay_s == null
          ? 2
          : photo.mechanical_vibration_delay_s
      );
      mechanicalDelay.disabled = !mechanicalAvailable;
    }

    if (mechanicalNote) {
      mechanicalNote.textContent = capabilities.strategy === 'bracket'
        ? 'Camera strategy: BRACKET — Unavailable with bracket capture.'
        : capabilities.strategy === 'sequential'
          ? 'Camera strategy: SEQUENTIAL — Delay is applied only after exposures of 1/60 s or slower.'
          : 'Camera strategy: unavailable until a characterized camera is assigned.';
    }

    if (isoComp) {
      isoComp.checked = photo.iso_compensation_enabled !== false;
    }

    populateRigIsoMaxSelect(
      isoMax,
      capabilities.iso_values,
      photo.iso_max == null ? 6400 : photo.iso_max
    );
  });

  const firstRig = rigs.find(rig => Number(rig.rig_id) === 1);
  const atmo = document.getElementById('cfg-atmo-switch');
  const atmoReplace = document.getElementById('cfg-atmo-replace-switch');
  if (atmo && firstRig) {
    atmo.checked = Boolean(firstRig.photo && firstRig.photo.atmos_enabled === true);
  }
  if (atmoReplace && firstRig) {
    atmoReplace.checked = Boolean(
      firstRig.photo && firstRig.photo.atmos_replace_enabled === true
    );
  }
  syncAtmosReplaceControl();
  refreshExposureOptRigVisibility();
}

async function loadRigPhotoConfig() {
  try {
    const response = await fetch('/api/rigs/photo');
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP error ${response.status}`);
    }
    renderRigPhotoConfig(payload);
  } catch (error) {
    flash(`CFG PHOTO RIG : ${error.message}`, 'red');
  }
}

function readRigPhotoConfig(rigId) {
  const antiBlur = document.getElementById(`rig-${rigId}-antiblur-switch`);
  const tolerance = document.getElementById(`rig-${rigId}-pixel-tolerance`);
  const mechanical = document.getElementById(`rig-${rigId}-mechanical-vibration-switch`);
  const mechanicalDelay = document.getElementById(`rig-${rigId}-mechanical-vibration-delay`);
  const isoComp = document.getElementById(`rig-${rigId}-iso-comp-switch`);

  const persistedRig = Array.isArray(rigPhotoState.rigs)
    ? rigPhotoState.rigs.find(
        rig => Number(rig.rig_id) === Number(rigId)
      )
    : null;
  const persistedPhoto = persistedRig && persistedRig.photo
    ? persistedRig.photo
    : {};
  const isoMax = document.getElementById(`rig-${rigId}-iso-max`);
  const atmo = document.getElementById('cfg-atmo-switch');
  const atmoReplace = document.getElementById('cfg-atmo-replace-switch');

  const toleranceValue = Number(tolerance && tolerance.value);
  const delayValue = Number(mechanicalDelay && mechanicalDelay.value);
  const isoMaxValue = Number(isoMax && isoMax.value);

  if (!Number.isFinite(toleranceValue) || toleranceValue <= 0) {
    throw new Error('Pixel tolerance must be strictly positive');
  }
  if (!Number.isInteger(delayValue) || delayValue < 0 || delayValue > 5) {
    throw new Error('Camera mechanical vibration delay must be an integer from 0 to 5 seconds');
  }
  if (!Number.isInteger(isoMaxValue) || isoMaxValue <= 0) {
    throw new Error('Invalid ISO Max');
  }

  return {
    rig_id: rigId,
    photo: {
      anti_trailing_enabled: Boolean(antiBlur && antiBlur.checked),
      motion_tolerance_px: toleranceValue,
      mechanical_vibration_enabled:
        mechanical && mechanical.disabled
          ? persistedPhoto.mechanical_vibration_enabled === true
          : Boolean(mechanical && mechanical.checked),
      mechanical_vibration_delay_s:
        mechanicalDelay && mechanicalDelay.disabled
          ? (
              Number.isInteger(Number(persistedPhoto.mechanical_vibration_delay_s))
                ? Number(persistedPhoto.mechanical_vibration_delay_s)
                : 2
            )
          : delayValue,
      iso_compensation_enabled: Boolean(isoComp && isoComp.checked),
      iso_max: isoMaxValue,
      atmos_enabled: Boolean(atmo && atmo.checked),
      atmos_replace_enabled: Boolean(
        atmo && atmo.checked && atmoReplace && atmoReplace.checked
      ),
    },
  };
}

async function persistRigPhoto(rigId) {
  try {
    const patch = readRigPhotoConfig(rigId);
    const response = await fetch('/api/rigs/photo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rigs: [patch]}),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP error ${response.status}`);
    }
    await loadRigPhotoConfig();
  } catch (error) {
    flash(`CFG PHOTO RIG ${rigId} : ${error.message}`, 'red');
    await loadRigPhotoConfig();
  }
}

function syncAtmosReplaceControl() {
  const atmo = document.getElementById('cfg-atmo-switch');
  const replace = document.getElementById('cfg-atmo-replace-switch');
  if (!replace) return;

  const enabled = Boolean(atmo && atmo.checked);
  replace.disabled = !enabled;
  if (!enabled) replace.checked = false;
}

function refreshExposureOptRigVisibility() {
  for (let rigId = 1; rigId <= 4; rigId += 1) {
    const column = document.getElementById(`camcfg-rig-column-${rigId}`);
    if (column) column.hidden = !_exposureOptRigIsActive(rigId);
  }
}

async function persistGlobalAtmos(enabled, showFeedback = true) {
  syncAtmosReplaceControl();
  const replace = document.getElementById('cfg-atmo-replace-switch');
  const replaceEnabled = Boolean(enabled && replace && replace.checked);

  try {
    const patches = [1, 2, 3, 4].map(rigId => ({
      rig_id: rigId,
      photo: {
        atmos_enabled: Boolean(enabled),
        atmos_replace_enabled: replaceEnabled,
      },
    }));

    const response = await fetch('/api/rigs/photo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rigs: patches}),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP error ${response.status}`);
    }

    await loadRigPhotoConfig();
    if (showFeedback) {
      flash(
        `Atmospheric Attenuation : ${enabled ? 'ON' : 'OFF'} for all RIGs`,
        'green'
      );
    }
  } catch (error) {
    flash(`Atmospheric Attenuation : ${error.message}`, 'red');
    await loadRigPhotoConfig();
  }
}

async function persistGlobalAtmosFromUi() {
  const control = document.getElementById('cfg-atmo-switch');
  syncAtmosReplaceControl();
  await persistGlobalAtmos(Boolean(control && control.checked));
}

async function persistGlobalAtmosReplaceFromUi() {
  const atmo = document.getElementById('cfg-atmo-switch');
  const replace = document.getElementById('cfg-atmo-replace-switch');
  if (!atmo || !atmo.checked) {
    if (replace) replace.checked = false;
    syncAtmosReplaceControl();
    return;
  }
  await persistGlobalAtmos(true);
}

async function loadExposureOptConfigList() {
  try {
    const r = await fetch('/api/configs/list_exposure_opt');
    const d = await r.json();
    const sel = document.getElementById('exposure-opt-config-select');
    if (!sel) return;

    const current = sel.value;
    sel.innerHTML = '<option value="">— Exposure Optimization file —</option>';

    (d.files || []).forEach(filename => {
      const opt = document.createElement('option');
      opt.value = filename;
      opt.textContent = filename;
      if (filename === current) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch(e) {}
}


function readExposureOptConfig() {
  const atmo = document.getElementById('cfg-atmo-switch');
  const atmoReplace = document.getElementById('cfg-atmo-replace-switch');
  const atmosEnabled = Boolean(atmo && atmo.checked);
  const atmosReplaceEnabled = Boolean(
    atmosEnabled && atmoReplace && atmoReplace.checked
  );

  const rigs = [1, 2, 3, 4].map(rigId => {
    const current = readRigPhotoConfig(rigId);
    const active = _exposureOptRigIsActive(rigId);

    return {
      rig_id: rigId,
      photo: {
        anti_trailing_enabled:
          active ? current.photo.anti_trailing_enabled : false,
        motion_tolerance_px: current.photo.motion_tolerance_px,
        mechanical_vibration_enabled:
          active ? current.photo.mechanical_vibration_enabled : false,
        mechanical_vibration_delay_s:
          current.photo.mechanical_vibration_delay_s,
        iso_compensation_enabled:
          active ? current.photo.iso_compensation_enabled : false,
        iso_max: current.photo.iso_max
      }
    };
  });

  return {
    schema_version: 1,
    config_type: 'exposure_optimization',
    atmospheric_attenuation_enabled: atmosEnabled,
    atmospheric_attenuation_replace_exposures: atmosReplaceEnabled,
    rigs
  };
}


async function saveExposureOptConfig() {
  const sel = document.getElementById('exposure-opt-config-select');
  const current = sel && sel.value
    ? sel.value.replace(/^expo_/, '').replace(/\.json$/, '')
    : 'exposure_opt';

  const name = prompt('Exposure Optimization file name:', current);
  if (!name) return;

  let data;
  try {
    data = readExposureOptConfig();
  } catch (e) {
    flash(`Exposure Optimization: ${e.message}`, 'red');
    return;
  }

  const save = async overwrite => {
    return fetch('/api/configs/save_exposure_opt', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({filename: name, data, overwrite})
    });
  };

  try {
    let r = await save(false);
    let d = await r.json();

    if (r.status === 409) {
      if (!confirm(`${d.filename || name} already exists. Overwrite it?`)) return;
      r = await save(true);
      d = await r.json();
    }

    if (r.ok && d.status === 'ok') {
      flash('Saved: ' + d.filename, 'green');
      await loadExposureOptConfigList();
      if (sel) sel.value = d.filename;
    } else {
      flash(d.error || 'Error', 'red');
    }
  } catch(e) {
    flash('Network error', 'red');
  }
}


async function loadExposureOptConfig(filename) {
  if (!filename) return;

  try {
    const r = await fetch(
      '/api/configs/load_exposure_opt/' + encodeURIComponent(filename)
    );

    const data = await r.json();

    if (!r.ok) {
      throw new Error(data.error || `HTTP error ${r.status}`);
    }

    if (data.config_type !== 'exposure_optimization') {
      throw new Error('Invalid Exposure Optimization file');
    }

    const atmos = Boolean(data.atmospheric_attenuation_enabled);
    const atmosReplace = Boolean(
      atmos && data.atmospheric_attenuation_replace_exposures
    );

    const patches = (data.rigs || []).map(rig => ({
      rig_id: Number(rig.rig_id),
      photo: {
        ...(rig.photo || {}),
        atmos_enabled: atmos,
        atmos_replace_enabled: atmosReplace
      }
    }));

    const post = await fetch('/api/rigs/photo', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({rigs: patches})
    });

    const result = await post.json();

    if (!post.ok) {
      throw new Error(result.error || `HTTP error ${post.status}`);
    }

    await loadRigPhotoConfig();
    flash('Exposure Optimization loaded: ' + filename, 'green');

  } catch(e) {
    flash(`Exposure Optimization: ${e.message}`, 'red');
  }
}


async function cleanExposureOptConfigs() {
  if (!confirm(
    'Delete ALL saved Exposure Optimization JSON files?\n\nThis cannot be undone.'
  )) return;

  try {
    const r = await fetch('/api/configs/exposure_opt/clean', {
      method: 'POST'
    });

    const d = await r.json();

    if (!r.ok) {
      throw new Error(d.error || `HTTP error ${r.status}`);
    }

    flash(`${d.deleted || 0} Exposure Optimization file(s) deleted`, 'yellow');
    await loadExposureOptConfigList();

  } catch(e) {
    flash(`Exposure Optimization CLEAN: ${e.message}`, 'red');
  }
}


let selectedRigId = 1;

let selectedTriggerRigId = 1;


function rigIsOperationallyActive(rig) {
  if (!rig) return false;

  const rigId = Number(rig.rig_id);

  return (
    rigId === 1 ||
    rig.enabled === true
  );
}


function firstOperationalRigId() {
  const rig = (rigDevicesState.rigs || []).find(
    candidate => rigIsOperationallyActive(candidate)
  );

  return rig
    ? Number(rig.rig_id)
    : null;
}


function selectedTriggerRig() {
  return rigDevicesState.rigs.find(
    rig =>
      Number(rig.rig_id) === selectedTriggerRigId &&
      rigIsOperationallyActive(rig)
  ) || null;
}

function activeTriggerRigIds() {
  return (rigDevicesState.rigs || [])
    .filter(rigIsOperationallyActive)
    .map(rig => Number(rig.rig_id));
}


function triggerClockSeconds(value) {
  if (!value || typeof value !== 'string') return null;

  const match = value.match(
    /^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$/
  );
  if (!match) return null;

  return (
    Number(match[1]) * 3600 +
    Number(match[2]) * 60 +
    Number(match[3])
  );
}


function triggerAstronomicalIcon(timestamp) {
  const circumstances = state.triggerCircumstances;
  if (!circumstances) return null;

  const eventRaw = triggerClockSeconds(timestamp);
  const tstartRaw = triggerClockSeconds(
    circumstances.TSTART || circumstances.tstart
  );
  const tendRaw = triggerClockSeconds(
    circumstances.TEND || circumstances.tend
  );
  const c1Raw = triggerClockSeconds(
    circumstances.C1 || circumstances.c1
  );
  const c2Raw = triggerClockSeconds(
    circumstances.C2 || circumstances.c2
  );
  const c3Raw = triggerClockSeconds(
    circumstances.C3 || circumstances.c3
  );
  const c4Raw = triggerClockSeconds(
    circumstances.C4 || circumstances.c4
  );

  if (
    eventRaw === null ||
    tstartRaw === null ||
    tendRaw === null ||
    c1Raw === null ||
    c4Raw === null
  ) {
    return null;
  }

  // Normalize a possible UTC midnight crossing relative to TSTART.
  const normalize = value => {
    if (value === null) return null;
    return value < tstartRaw ? value + 86400 : value;
  };

  const tstart = tstartRaw;
  const tend = normalize(tendRaw);
  const c1 = normalize(c1Raw);
  const c2 = normalize(c2Raw);
  const c3 = normalize(c3Raw);
  const c4 = normalize(c4Raw);

  let event = eventRaw;

  if (tend > 86400 && event < tstart) {
    event += 86400;
  }

  if (event < tstart || event > tend) {
    return null;
  }

  // Partial eclipse: no C2/C3.
  if (c2 === null && c3 === null) {
    if (event < c1) return '☀️';
    if (event < c4) return '🌙';
    return '☀️';
  }

  if (c2 === null || c3 === null) {
    return null;
  }

  const duration = Number(state.triggerDiamondDurationS);
  const diamondDuration =
    Number.isFinite(duration) && duration >= 0
      ? duration
      : 0;

  const diamondBefore = c2 - diamondDuration;
  const diamondAfter = c3 + diamondDuration;

  if (event < c1) return '☀️';
  if (event < diamondBefore) return '🌙';
  if (event < c2) return '💍';
  if (event < c3) return '🌑';
  if (event < diamondAfter) return '💍';
  if (event < c4) return '🌙';
  return '☀️';
}


function triggerLogIcon(level) {
  const icons = {
    warning: '☀️',
    orange: '🌙',
    purple: '💍',
    totality: '🌑',
    audio: '🔊',
    phase: '◆',
    error: '❌',
    success: '✅',
    gps: '⚙️',
    info: '•',
  };

  return icons[level] || '•';
}


const triggerLogEntries = {
  1: [],
  2: [],
  3: [],
  4: [],
};


function triggerLogRigId(entry) {
  let rigId = Number(entry && entry.rig_id);

  // Historical entries written before multi-RIG log ownership existed
  // belong to the legacy RIG 1 stream.
  if (!Number.isInteger(rigId) || rigId < 1 || rigId > 4) {
    rigId = 1;
  }

  return rigId;
}


function triggerLogLineElement(entry, rigId) {
  const div = document.createElement('div');
  div.className = `log-line ${entry.level || 'info'}`;

  const timestamp = entry.timestamp || '--:--:--';
  const icon =
    triggerAstronomicalIcon(entry.timestamp) ||
    triggerLogIcon(entry.level);

  div.textContent =
    `[${timestamp}][RIG${rigId}][${icon}] ${entry.text || ''}`;

  return div;
}


function renderTriggerLog() {
  const container = document.getElementById('log-container-trigger');
  const title = document.getElementById('trigger-log-title');

  if (title) {
    title.textContent = `Trigger log — RIG ${selectedTriggerRigId}`;
  }

  if (!container) return;

  container.innerHTML = '';

  const entries = triggerLogEntries[selectedTriggerRigId] || [];

  entries.forEach(entry => {
    container.appendChild(
      triggerLogLineElement(entry, selectedTriggerRigId)
    );
  });

  container.scrollTop = container.scrollHeight;
}


function clearTriggerRigLog(rigId = selectedTriggerRigId) {
  const numericRigId = Number(rigId);

  if (!Number.isInteger(numericRigId) || numericRigId < 1 || numericRigId > 4) {
    return;
  }

  triggerLogEntries[numericRigId] = [];

  if (numericRigId === selectedTriggerRigId) {
    renderTriggerLog();
  }
}


function _logNearBottom(container, thresholdPx = 32) {
  if (!container) return true;
  return (
    container.scrollHeight
    - container.scrollTop
    - container.clientHeight
  ) <= thresholdPx;
}


function appendTriggerRigLog(entry) {
  if (_logPaused || !entry) return;

  const rigId = triggerLogRigId(entry);

  triggerLogEntries[rigId].push(entry);

  if (rigId !== selectedTriggerRigId) return;

  const container = document.getElementById('log-container-trigger');
  if (!container) return;

  const followTail = _logNearBottom(container);
  container.appendChild(triggerLogLineElement(entry, rigId));

  if (followTail) {
    container.scrollTop = container.scrollHeight;
  }
}


function renderTriggerRigSelection() {
  renderTriggerLog();

  const activeRigIds = activeTriggerRigIds();
  const multiRig = activeRigIds.length > 1;

  const debugButton = document.getElementById('btn-debug');
  const dryRunButton = document.getElementById('btn-dryrun');

  if (debugButton) {
    debugButton.textContent = multiRig
      ? '🧪 DEBUG ALL'
      : '🧪 DEBUG';
  }

  if (dryRunButton) {
    dryRunButton.textContent = multiRig
      ? '🧪 DRY-RUN ALL'
      : '🧪 DRY-RUN';
  }

  let selectedRig = selectedTriggerRig();

  if (!selectedRig) {
    selectedTriggerRigId = firstOperationalRigId();
    selectedRig = selectedTriggerRig();
  }

  DEFAULT_RIGS.forEach(defaultRig => {
    const rig = rigDevicesState.rigs.find(
      candidate => Number(candidate.rig_id) === defaultRig.rig_id
    );

    const available = rigIsOperationallyActive(rig);

    const button = document.getElementById(
      `trigger-rig-${defaultRig.rig_id}`
    );

    if (!button) return;

    button.hidden = !available;
    button.disabled = !available;

    if (available) {
      const defaultName = `RIG ${defaultRig.rig_id}`;
      const rigName = (
        typeof rig.name === 'string' && rig.name.trim()
          ? rig.name.trim()
          : defaultName
      );

      button.textContent = rigName === defaultName
        ? defaultName
        : `${defaultName} — ${rigName}`;
    }

    button.classList.toggle(
      'active',
      available && selectedTriggerRigId === defaultRig.rig_id
    );

    button.setAttribute(
      'aria-pressed',
      available && selectedTriggerRigId === defaultRig.rig_id
        ? 'true'
        : 'false'
    );
  });

  const targetLabel = document.getElementById(
    'trigger-target-label'
  );

  if (!targetLabel) return;

  const rig = selectedTriggerRig();

  if (!rig) {
    targetLabel.textContent = 'No RIG selected';
    return;
  }

  const camera = rig.devices && rig.devices.camera;

  targetLabel.textContent = camera
    ? `RIG ${selectedTriggerRigId} — Camera : ${rigDeviceDisplayLabel('camera', camera)}`
    : `RIG ${selectedTriggerRigId} — No camera`;
}

function selectTriggerRig(rigId) {
  const numericRigId = Number(rigId);

  const rig = rigDevicesState.rigs.find(
    candidate => Number(candidate.rig_id) === numericRigId
  );

  if (!rigIsOperationallyActive(rig)) return;

  selectedTriggerRigId = numericRigId;
  renderTriggerRigSelection();
  updateSelectedTriggerPhase();
  loadTriggerConfigList();
}


function selectedControlsRig() {
  return rigDevicesState.rigs.find(
    rig =>
      Number(rig.rig_id) === selectedRigId &&
      rigIsOperationallyActive(rig)
  ) || null;
}

function selectedPilotableMountRig() {
  const rig = selectedControlsRig();
  const mount = rig && rig.devices && rig.devices.mount;
  return mount && ![null, '', 'none', 'external'].includes(mount.backend) ? rig : null;
}

function selectedPilotableFocuserRig() {
  const rig = selectedControlsRig();
  const focuser = rig && rig.devices && rig.devices.focuser;
  const backend = focuser && (focuser.backend || focuser.plugin);
  return focuser && ![null, '', 'none'].includes(backend) ? rig : null;
}

function renderSelectedFocuserAvailability() {
  const focuserSection = document.getElementById('focuser-section');
  if (!focuserSection) return;
  const focuserAvailable = Boolean(selectedPilotableFocuserRig());
  focuserSection.hidden = !focuserAvailable;
  focuserSection.setAttribute('aria-disabled', focuserAvailable ? 'false' : 'true');
  focuserSection.querySelectorAll('button, input, select').forEach(control => {
    control.disabled = !focuserAvailable;
  });
}

function renderSelectedMountAvailability() {
  const mountSection = document.getElementById('mount-section');
  if (!mountSection) return;
  const mountAvailable = Boolean(selectedPilotableMountRig());
  mountSection.hidden = !mountAvailable;
  mountSection.setAttribute('aria-disabled', mountAvailable ? 'false' : 'true');
  mountSection.querySelectorAll('button, input, select').forEach(control => {
    control.disabled = !mountAvailable;
  });
}

function renderControlsRigSelection() {
  let selectedRig = selectedControlsRig();

  if (!selectedRig) {
    selectedRigId = firstOperationalRigId();
    selectedRig = selectedControlsRig();
  }

  DEFAULT_RIGS.forEach(defaultRig => {
    const rig = rigDevicesState.rigs.find(
      candidate => Number(candidate.rig_id) === defaultRig.rig_id
    );

    const available = rigIsOperationallyActive(rig);
    const button = document.getElementById(`controls-rig-${defaultRig.rig_id}`);
    if (!button) return;
    button.hidden = !available;
    button.disabled = !available;

    if (available) {
      const defaultName = `RIG ${defaultRig.rig_id}`;
      const rigName = (
        typeof rig.name === 'string' && rig.name.trim()
          ? rig.name.trim()
          : defaultName
      );
      button.textContent = rigName === defaultName
        ? defaultName
        : `${defaultName} — ${rigName}`;
    }

    button.classList.toggle('active', available && selectedRigId === defaultRig.rig_id);
    button.setAttribute('aria-pressed', available && selectedRigId === defaultRig.rig_id ? 'true' : 'false');
  });
  document.dispatchEvent(new CustomEvent('controlsrigchange'));
  renderSelectedMountAvailability();
  renderSelectedFocuserAvailability();

  const targetLabel = document.getElementById('controls-target-label');
  if (!targetLabel) return;
  const rig = selectedControlsRig();
  if (!rig) {
    targetLabel.textContent = 'No RIG selected';
    return;
  }
  const mount = rig.devices && rig.devices.mount;
  const pilotable = mount && ![null, '', 'none', 'external'].includes(mount.backend);
  targetLabel.textContent = pilotable
    ? `RIG ${selectedRigId} — Mount : ${mount.display_label}`
    : `RIG ${selectedRigId} — No controllable mount`;
}

function selectControlsRig(rigId) {
  const numericRigId = Number(rigId);

  const rig = rigDevicesState.rigs.find(
    candidate => Number(candidate.rig_id) === numericRigId
  );

  if (!rigIsOperationallyActive(rig)) return;

  selectedRigId = numericRigId;
  renderControlsRigSelection();
}

function escapeDeviceText(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function deviceOptions(category, device) {
  const values = ['none', ...DEVICE_PLUGIN_OPTIONS[category]];
  [device.plugin, device.suggested_plugin].forEach(value => {
    if (value && !values.includes(value)) values.push(value);
  });
  return values.map(value => {
    const escaped = escapeDeviceText(value);
    return `<option value="${escaped}">${value === 'none' ? 'None' : escaped}</option>`;
  }).join('');
}

function renderDevices(devices) {
  const list = document.getElementById('devices-list');
  if (!list) return;

  const gpsDevice = devices.gps || {};
  state.gpsDeviceDetected = gpsDevice.detected === true;
  list.innerHTML = ['gps'].map(category => {
    const device = devices[category] || {plugin: 'none', active: false};
    const active = device.active === true;
    const detected = device.detected === true ? 'Detected' : 'Not detected';
    const suggestion = escapeDeviceText(device.suggested_plugin || 'none');
    return `<div class="card device-card ${active ? 'active' : ''}" data-device-category="${category}">
      <div class="card-title" style="display:flex;justify-content:space-between">
        <span>${DEVICE_LABELS[category]}</span><span>${active ? 'ACTIVE' : 'INACTIVE'}</span>
      </div>
      <div class="field">
        <label for="device-${category}-select">Plugin</label>
        <div class="select-chev">
          <select id="device-${category}-select" onchange="selectDevice('${category}', this.value)">
            ${deviceOptions(category, device)}
          </select>
        </div>
      </div>
      <div class="device-status" style="margin-top:8px">${detected} · suggestion : ${suggestion}</div>
    </div>`;
  }).join('');
  ['gps'].forEach(category => {
    const device = devices[category] || {};
    const select = document.getElementById(`device-${category}-select`);
    if (select) select.value = device.plugin || 'none';
    document.querySelectorAll(`[data-device-section="${category}"]`).forEach(section => {
      section.hidden = device.active !== true;
    });
  });

  // La carte Sync GPS doit refléter immédiatement la détection faite
  // dans l'onglet Devices, sans attendre une opération de synchronisation.
  if (state.gps) updateGPS(state.gps);
}

function rigDeviceIdentity(device) {
  if (!device) return null;
  if (device.serial && !/^usb:\d+,\d+$/.test(device.serial)) return `serial:${device.serial}`;
  if (typeof device.device_id === 'string' && device.device_id.trim()) {
    return `device_id:${device.device_id.trim()}`;
  }
  if (device.fallback_physical_path) return `fallback:${device.fallback_physical_path}`;
  return null;
}

function persistedRigBinding(device) {
  if (!device) return null;
  const runtimeFields = new Set([
    'present', 'pilotable', 'display_label', 'transport_locator',
    'busnum', 'devnum', 'connected', 'categories',
    'driver_interface', 'driver_name', 'driver_version'
  ]);
  return Object.fromEntries(Object.entries(device).filter(([key]) => !runtimeFields.has(key)));
}

function encodedRigBinding(device) {
  return device ? encodeURIComponent(JSON.stringify(persistedRigBinding(device))) : '';
}

function rigDeviceDisplayLabel(category, device) {
  if (!device) return 'Unknown';

  let label =
    device.display_label
    || device.model
    || device.serial
    || device.backend
    || 'Unknown';

  if (category === 'camera' && device.serial) {
    const serial = String(device.serial);
    const suffix = serial.slice(-3);
    if (suffix && !String(label).includes(suffix)) {
      label += ` · #${suffix}`;
    }
  }

  return label;
}

function renderRigDevices(payload, inventoryOverride) {
  const rigs = Array.isArray(payload.rigs) ? payload.rigs : DEFAULT_RIGS;
  const inventory = inventoryOverride || payload.inventory || rigDevicesState.inventory;
  rigDevicesState = {rigs, inventory};

  const assignments = {};
  rigs.forEach(rig => RIG_DEVICE_CATEGORIES.forEach(category => {
    const identity = rigDeviceIdentity(rig.devices && rig.devices[category]);
    if (identity) assignments[`${category}:${identity}`] = Number(rig.rig_id);
  }));

  rigs.forEach(rig => {
    const rigId = Number(rig.rig_id);
    const body = document.getElementById(`rig-body-${rigId}`);
    if (!body) return;
    const rigName = (
      typeof rig.name === 'string' && rig.name.trim()
        ? rig.name.trim()
        : `RIG ${rigId}`
    );

    body.innerHTML = `
      <div class="field rig-name-field">
        <label for="rig-${rigId}-name">RIG NAME</label>
        <input
          type="text"
          id="rig-${rigId}-name"
          maxlength="64"
          value="${escapeDeviceText(rigName)}"
          data-persisted-value="${escapeDeviceText(rigName)}"
          onchange="persistRigName(${rigId}, this)"
        >
      </div>
    ` + RIG_DEVICE_CATEGORIES.map(category => {
      const current = rig.devices && rig.devices[category];
      const choices = [...(inventory[category] || [])];

      if (category === 'mount') {
        choices.unshift({
          backend: 'external',
          control: 'external',
          geometry: 'altaz',
          model: 'External Alt-Az',
          display_label: 'External Alt-Az',
          pilotable: false,
        });
      }

      if (current && !choices.some(choice =>
        encodedRigBinding(choice) === encodedRigBinding(current)
      )) {
        choices.push(current);
      }

      const options = ['<option value="">None</option>'];
      choices.forEach(choice => {
        const identity = rigDeviceIdentity(choice);
        const assignedRig = identity ? assignments[`${category}:${identity}`] : null;
        const isCurrent = current
          && encodedRigBinding(choice) === encodedRigBinding(current);
        const isExternalAltAz = (
          category === 'mount'
          && choice.backend === 'external'
          && choice.geometry === 'altaz'
        );

        if (assignedRig && assignedRig !== rigId) return;

        let label = rigDeviceDisplayLabel(category, choice);
        if (isCurrent && current.present === false) label += ' — expected / not detected';
        if (choice.pilotable === false && !isExternalAltAz) label += ' — not controllable';

        const optionBinding = isCurrent ? current : choice;
        const disabled = choice.pilotable === false && !isExternalAltAz;
        options.push(`<option value="${escapeDeviceText(encodedRigBinding(optionBinding))}"${disabled ? ' disabled' : ''}>${escapeDeviceText(label)}</option>`);
      });
      const persisted = encodedRigBinding(current);
      let block = `<div class="rig-device" data-rig-device="${category}">
        <div class="card-title">${DEVICE_LABELS[category]}</div>
        <div class="field">
          <div class="select-chev"><select id="rig-${rigId}-${category}-select" aria-label="${DEVICE_LABELS[category]} RIG ${rigId}" data-persisted-value="${escapeDeviceText(persisted)}" onchange="selectRigDevice(${rigId}, '${category}', this)">
            ${options.join('')}
          </select></div>
        </div>
      </div>`;

      if (category === 'camera') {
        const focal = rig.optics && rig.optics.focal_length_mm;
        const focalValue = focal == null ? '' : String(focal);

        block += `<div class="rig-device rig-optics" data-rig-optics>
          <div class="card-title">Optics</div>
          <div class="field">
            <label for="rig-${rigId}-focal">Focal length (mm)</label>
            <input
              type="text"
              inputmode="decimal"
              id="rig-${rigId}-focal"
              placeholder="mm"
              value="${escapeDeviceText(focalValue)}"
              data-persisted-value="${escapeDeviceText(focalValue)}"
              onchange="persistRigFocalLength(${rigId}, this)"
            >
          </div>
        </div>`;
      }

      return block;
    }).join('');
    RIG_DEVICE_CATEGORIES.forEach(category => {
      const select = document.getElementById(`rig-${rigId}-${category}-select`);
      if (select) select.value = select.dataset.persistedValue;
    });
  });
  updateRigs(rigs);
  updateControlsVisibility();
}

async function loadRigDevices(inventoryOverride) {
  try {
    const requests = [fetch('/api/rigs/devices')];
    if (!inventoryOverride) requests.push(fetch('/api/rigs/devices/inventory'));
    const responses = await Promise.all(requests);
    const payload = await responses[0].json();
    const inventory = inventoryOverride || await responses[1].json();
    if (!responses[0].ok) throw new Error(payload.error || `HTTP error ${responses[0].status}`);
    if (!inventoryOverride && !responses[1].ok) throw new Error(inventory.error || `HTTP error ${responses[1].status}`);
    renderRigDevices(payload, inventory);
    await loadRigPhotoConfig();
  } catch (error) {
    flash(`Devices RIG : ${error.message}`, 'red');
  }
}



function renderRigCameraBattery(element, value) {
  if (!element) return;

  element.style.color = 'var(--text-dim)';

  if (value === null || value === undefined || value === '') {
    element.textContent = '—';
    return;
  }

  let numeric = null;

  if (typeof value === 'number') {
    numeric = value;
  } else {
    const match = String(value).match(/(\d+(?:\.\d+)?)/);
    if (match) numeric = Number(match[1]);
  }

  if (numeric === null || !Number.isFinite(numeric)) {
    element.textContent = String(value);
    return;
  }

  numeric = Math.max(0, Math.min(100, numeric));
  element.textContent = `${Math.round(numeric)}%`;

  if (numeric <= 20) {
    element.style.color = 'var(--red)';
  } else if (numeric <= 50) {
    element.style.color = 'var(--orange)';
  } else {
    element.style.color = 'var(--green)';
  }
}


async function readRigCameraInfo(rigId, button) {
  const column = document.getElementById(`cam-rig-column-${rigId}`);
  if (!column) return;

  const lastRead = column.querySelector('.cam-rig-last-read');

  if (button) button.disabled = true;

  try {
    const response = await fetch(`/api/rigs/${rigId}/camera/read_info`, {
      method: 'POST',
    });

    const responseText = await response.text();
    let data = {};
    if (responseText) {
      try {
        data = JSON.parse(responseText);
      } catch (_error) {
        throw new Error(`HTTP ${response.status}: invalid server response`);
      }
    }

    if (!response.ok) {
      throw new Error(data.error || `HTTP error ${response.status}`);
    }

    const vendor = column.querySelector('.cam-rig-vendor');
    if (vendor) {
      const vendorValue =
        data.manufacturer
        || data.vendor
        || data.brand;
      if (vendorValue) vendor.textContent = vendorValue;
    }

    const model = column.querySelector('.cam-rig-model');
    if (model && data.model) {
      model.textContent = data.model;
    }

    const battery = column.querySelector('.cam-rig-battery');
    renderRigCameraBattery(battery, data.battery);

    if (lastRead) {
      lastRead.textContent = new Date().toISOString().slice(11, 19) + ' UTC';
    }

    flash(`RIG ${rigId} camera information read`, 'green');
  } catch (error) {
    flash(`RIG ${rigId} camera: ${error.message}`, 'red');
  } finally {
    if (button) button.disabled = false;
  }
}


async function testRigCameraPhoto(rigId, button) {
  const column = document.getElementById(`cam-rig-column-${rigId}`);
  if (!column) return;

  if (button) button.disabled = true;

  try {
    const response = await fetch(`/api/rigs/${rigId}/camera/test_photo`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({speed: '1/125'}),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `HTTP error ${response.status}`);
    }

    flash(`RIG ${rigId} test photo completed`, 'green');
  } catch (error) {
    if (status) status.textContent = 'Test photo failed';
    flash(`RIG ${rigId} camera: ${error.message}`, 'red');
  } finally {
    if (button) button.disabled = false;
  }
}


async function persistRigName(rigId, input) {
  const persistedValue = input.dataset.persistedValue || `RIG ${rigId}`;
  const name = input.value.trim() || `RIG ${rigId}`;

  input.value = name;
  input.disabled = true;

  try {
    const response = await fetch('/api/rigs/devices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rigs: [{rig_id: rigId, name: name}],
      }),
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || `HTTP error ${response.status}`);
    }

    await loadRigDevices();
  } catch (error) {
    input.value = persistedValue;
    flash(`RIG ${rigId} name: ${error.message}`, 'red');
  } finally {
    input.disabled = false;
  }
}


async function persistRigFocalLength(rigId, input) {
  const persistedValue = input.dataset.persistedValue || '';
  const raw = input.value.trim();

  let focal = null;

  if (raw !== '') {
    focal = Number(raw);

    if (!Number.isFinite(focal) || focal <= 0) {
      input.value = persistedValue;
      flash(
        `RIG ${rigId} focal length must be strictly positive`,
        'red'
      );
      return;
    }
  }

  input.disabled = true;

  try {
    const response = await fetch('/api/rigs/devices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rigs: [{
          rig_id: rigId,
          optics: {
            focal_length_mm: focal
          }
        }]
      }),
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(
        result.error || `HTTP error ${response.status}`
      );
    }

    await loadRigDevices();

  } catch (error) {
    input.value = persistedValue;
    flash(
      `RIG ${rigId} focal length: ${error.message}`,
      'red'
    );

  } finally {
    input.disabled = false;
  }
}


async function selectRigDevice(rigId, category, select) {
  const persistedValue = select.dataset.persistedValue || '';
  let binding = null;
  try {
    binding = select.value ? JSON.parse(decodeURIComponent(select.value)) : null;
    const response = await fetch('/api/rigs/devices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rigs: [{rig_id: rigId, devices: {[category]: binding}}]}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP error ${response.status}`);
    await loadRigDevices();
  } catch (error) {
    select.value = persistedValue;
    flash(`Devices RIG : ${error.message}`, 'red');
  }
}

function updateRigs(rigs) {
  const updatedRigs = Array.isArray(rigs) ? rigs : DEFAULT_RIGS;
  const cachedById = new Map(rigDevicesState.rigs.map(rig => [Number(rig.rig_id), rig]));
  rigDevicesState.rigs = updatedRigs.map(rig => ({
    ...(cachedById.get(Number(rig.rig_id)) || {}),
    ...rig,
  }));
  const byId = new Map(
    rigDevicesState.rigs.map(rig => [Number(rig.rig_id), rig])
  );
  DEFAULT_RIGS.forEach(defaultRig => {
    const rig = byId.get(defaultRig.rig_id) || defaultRig;
    const rigName = (
      typeof rig.name === 'string' && rig.name.trim()
        ? rig.name.trim()
        : `RIG ${defaultRig.rig_id}`
    );
    const triggerEnabled = defaultRig.rig_id === 1 || rig.enabled === true;
    const column = document.getElementById(`rig-column-${defaultRig.rig_id}`);
    const cameraColumn = document.getElementById(`camcfg-rig-column-${defaultRig.rig_id}`);
    const cameraRigColumn = document.getElementById(`cam-rig-column-${defaultRig.rig_id}`);
    const toggle = document.getElementById(`rig-switch-${defaultRig.rig_id}`);
    if (column) column.classList.toggle('enabled', triggerEnabled);
    if (cameraColumn) {
      cameraColumn.classList.toggle('enabled', triggerEnabled);

      /*
       * Exposure Optimization only displays participating RIGs.
       * Apply visibility immediately here as well as in
       * refreshExposureOptRigVisibility(), so async configuration reloads
       * cannot briefly reveal inactive RIGs and cause layout flicker.
       */
      cameraColumn.hidden = !triggerEnabled;

      const title = cameraColumn.querySelector('.card-title');
      if (title) {
        const defaultName = `RIG ${defaultRig.rig_id}`;
        title.textContent = rigName === defaultName
          ? defaultName
          : `${defaultName} — ${rigName}`;
      }
    }
    if (cameraRigColumn) {
      cameraRigColumn.classList.toggle('enabled', triggerEnabled);

      /*
       * Camera is an operational screen:
       * inactive RIGs must not be displayed.
       */
      cameraRigColumn.hidden = !triggerEnabled;

      const defaultName = `RIG ${defaultRig.rig_id}`;

      const title = cameraRigColumn.querySelector('.cam-rig-title');
      if (title) title.textContent = defaultName;

      const nameElement = cameraRigColumn.querySelector('.cam-rig-name');
      if (nameElement) nameElement.textContent = rigName;

      const camera = rig.devices && rig.devices.camera;

      const vendorElement = cameraRigColumn.querySelector('.cam-rig-vendor');
      if (vendorElement) {
        vendorElement.textContent = camera
          ? (camera.manufacturer || camera.vendor || camera.brand || '—')
          : '—';
      }

      const modelElement = cameraRigColumn.querySelector('.cam-rig-model');
      if (modelElement) {
        modelElement.textContent = camera
          ? (camera.model || camera.display_label || '—')
          : '—';
      }
    }
    if (toggle) toggle.checked = rig.enabled === true;
  });
  renderControlsRigSelection();
  renderTriggerRigSelection();
}

document.addEventListener('change', async event => {
  const toggle = event.target.closest('.rig-switch');
  if (!toggle) return;

  const column = toggle.closest('.rig-column');
  const rigId = Number(column && column.dataset.rigId);
  if (!Number.isInteger(rigId) || rigId < 2 || rigId > 4) return;

  const requestedEnabled = toggle.checked;
  const previousEnabled = !requestedEnabled;

  if (column) column.classList.toggle('enabled', requestedEnabled);
  toggle.disabled = true;

  try {
    const response = await fetch('/api/rigs/devices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rigs: [{rig_id: rigId, enabled: requestedEnabled}],
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `HTTP error ${response.status}`);
    }
    await loadRigDevices();
  } catch (error) {
    toggle.checked = previousEnabled;
    if (column) column.classList.toggle('enabled', previousEnabled);
    flash(`Participation trigger RIG ${rigId} : ${error.message}`, 'red');
  } finally {
    toggle.disabled = false;
  }
});

function updateControlsVisibility(devices) {
  if (devices && typeof devices === 'object') {
    globalDevicesState = devices;
  }

  // Controls is always available because it contains global controls
  // such as audio, independently of any selected RIG/device.
  const controlsTab = document.getElementById('controls-tab');
  const controlsPanel = document.getElementById('controls-panel');

  if (controlsTab) controlsTab.hidden = false;
  if (controlsPanel) controlsPanel.hidden = false;

  renderControlsRigSelection();
}

async function fetchDevices() {
  try {
    const response = await fetch('/api/devices');
    const devices = await response.json();
    if (!response.ok) throw new Error(devices.error || `HTTP error ${response.status}`);
    renderDevices(devices);
    updateControlsVisibility(devices);
  } catch (error) {
    flash(`Devices : ${error.message}`, 'red');
  }
}

async function selectDevice(category, plugin) {
  try {
    const response = await fetch('/api/devices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({[category]: plugin}),
    });
    const devices = await response.json();
    if (!response.ok) throw new Error(devices.error || `HTTP error ${response.status}`);
    renderDevices(devices);
    updateControlsVisibility(devices);
    flash(`${DEVICE_LABELS[category]} updated`, 'green');
  } catch (error) {
    flash(`Devices : ${error.message}`, 'red');
    fetchDevices();
  }
}

async function rescanDevices() {
  const button = document.getElementById('devices-rescan');
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/devices/detect', {method: 'POST'});
    const devices = await response.json();
    if (!response.ok) throw new Error(devices.error || `HTTP error ${response.status}`);
    renderDevices(devices);
    updateControlsVisibility(devices);
    flash('Device detection completed', 'green');
  } catch (error) {
    flash(`Detection: ${error.message}`, 'red');
  } finally {
    if (button) button.disabled = false;
  }
}

function waitForBrowserPaint() {
  return new Promise(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

async function refreshRigDevices(silent = false) {
  if (deviceAutoRefreshInFlight) return;

  deviceAutoRefreshInFlight = true;
  const buttons = document.querySelectorAll('#devices-rescan, #add-camera-rescan');
  buttons.forEach(button => { button.disabled = true; });

  // Do not let a slow USB inventory postpone the disabled visual state.
  await waitForBrowserPaint();

  try {
    const [inventoryResponse, devicesResponse] = await Promise.all([
      fetch('/api/rigs/devices/refresh', {method: 'POST'}),
      fetch('/api/devices/detect', {method: 'POST'}),
    ]);
    const inventory = await inventoryResponse.json();
    const devices = await devicesResponse.json();
    if (!inventoryResponse.ok) {
      throw new Error(inventory.error || `HTTP error ${inventoryResponse.status}`);
    }
    if (!devicesResponse.ok) {
      throw new Error(devices.error || `HTTP error ${devicesResponse.status}`);
    }
    await loadRigDevices(inventory);
    renderDevices(devices);
    updateControlsVisibility(devices);
    await pollCameraCharacterization();
    await pollCameraValidation();
    if (!silent) flash('Device inventory refreshed', 'green');
  } catch (error) {
    flash(`Detection: ${error.message}`, 'red');
  } finally {
    buttons.forEach(button => { button.disabled = false; });
    deviceAutoRefreshInFlight = false;
  }
}

function startDeviceAutoRefresh() {
  setInterval(() => {
    refreshRigDevices(true);
  }, DEVICE_AUTO_REFRESH_INTERVAL_MS);
}

const cameraAddLogState = {
  characterization: [],
  validation: [],
  systemUpdate: [],
  solarTriggerUpdate: [],
  characterizationOffset: 0,
  validationOffset: 0,
  systemUpdateOffset: 0,
  solarTriggerUpdateOffset: 0,
  characterizationResult: '',
  clearedCharacterizationResult: '',
};

function cameraLogLineClass(line) {
  const text = String(line || '');
  if (/\b(SUCCESS|PASS|PASSED|COMPLETED SUCCESSFULLY)\b/i.test(text)) return 'camera-log-success';
  if (/\b(ERROR|FAILED|FAIL|TIMEOUT|CANCELLED)\b/i.test(text)) return 'camera-log-error';
  return '';
}

function renderCameraAddLog() {
  const log = document.getElementById('camera-add-log');
  if (!log) return;
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  const sections = [];
  const add = (title, values) => {
    const lines = (values || []).filter(Boolean).map(String);
    if (lines.length) sections.push([title, ...lines]);
  };
  const characterization = cameraAddLogState.characterization.slice(cameraAddLogState.characterizationOffset);
  const validation = cameraAddLogState.validation.slice(cameraAddLogState.validationOffset);
  const result = cameraAddLogState.characterizationResult &&
    cameraAddLogState.characterizationResult !== cameraAddLogState.clearedCharacterizationResult
      ? cameraAddLogState.characterizationResult : '';
  add('=== CAMERA CHARACTERIZATION ===', [...characterization, result]);
  add('=== CAMERA VALIDATION ===', validation);
  add('=== UPDATE SYSTEM ===', cameraAddLogState.systemUpdate.slice(cameraAddLogState.systemUpdateOffset));
  add('=== UPDATE SOLAR ECLIPSE TRIGGER ===', cameraAddLogState.solarTriggerUpdate.slice(cameraAddLogState.solarTriggerUpdateOffset));

  log.replaceChildren();
  sections.forEach((section, sectionIndex) => {
    section.forEach((line, lineIndex) => {
      const span = document.createElement('span');
      span.textContent = line;
      if (lineIndex > 0) span.className = cameraLogLineClass(line);
      log.appendChild(span);
      log.appendChild(document.createTextNode('\n'));
    });
    if (sectionIndex < sections.length - 1) log.appendChild(document.createTextNode('\n'));
  });
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function appendCameraAddLogLine(source, line) {
  const values = Array.isArray(cameraAddLogState[source])
    ? cameraAddLogState[source]
    : [];
  values.push(String(line));
  cameraAddLogState[source] = values;
  renderCameraAddLog();
}

function updateCameraAddLog(source, lines, result = null) {
  const normalized = Array.isArray(lines) ? lines.map(String) : [];
  const offsetKey = `${source}Offset`;

  if (normalized.length < cameraAddLogState[offsetKey]) {
    cameraAddLogState[offsetKey] = 0;
  }

  cameraAddLogState[source] = normalized;

  if (source === 'characterization') {
    cameraAddLogState.characterizationResult = result
      ? JSON.stringify(result, null, 2)
      : '';
  }

  renderCameraAddLog();
}

function clearCameraAddLog() {
  cameraAddLogState.characterizationOffset =
    cameraAddLogState.characterization.length;
  cameraAddLogState.validationOffset =
    cameraAddLogState.validation.length;
  cameraAddLogState.systemUpdateOffset =
    cameraAddLogState.systemUpdate.length;
  cameraAddLogState.solarTriggerUpdateOffset =
    cameraAddLogState.solarTriggerUpdate.length;
  cameraAddLogState.clearedCharacterizationResult =
    cameraAddLogState.characterizationResult;
  renderCameraAddLog();
}

let cameraCharacterizationQuestion = null;
let cameraCharacterizationPolling = false;
let cameraCharacterizationStarting = false;
let cameraCharacterizationStartingMessage = '';
let cameraQualificationLocator = '';
let cameraQualificationAutoValidationJob = '';

function renderCameraCharacterizationStatus(status) {
  const select = document.getElementById('camera-characterization-select');
  if (!select) return;

  if (Array.isArray(status.qualification_candidates)) {
    const selected = select.value;
    select.replaceChildren();
    for (const entry of status.qualification_candidates) {
      const option = document.createElement('option');
      option.value = entry.transport_locator || '';
      option.dataset.characterized = entry.characterized ? '1' : '0';
      const label = entry.display_label || entry.model || option.value;
      option.textContent = entry.characterized ? label : `NEW · ${label}`;
      if (!entry.characterized) option.classList.add('camera-new-option');
      select.appendChild(option);
    }
    if ([...select.options].some(option => option.value === selected)) select.value = selected;
  }

  const characterizationBusy = Boolean(status.running) || cameraCharacterizationStarting;
  const validationBusy = Boolean(window.cameraValidationRunning) || cameraValidationStarting;
  select.disabled = characterizationBusy || validationBusy;
  document.getElementById('camera-characterization-start').disabled =
    characterizationBusy || validationBusy || !select.options.length;
  document.getElementById('camera-characterization-cancel').disabled =
    !status.running && !window.cameraValidationRunning;

  const characterizationLogs = Array.isArray(status.logs)
    ? status.logs.slice()
    : [];
  if (
    cameraCharacterizationStarting
    && cameraCharacterizationStartingMessage
    && !characterizationLogs.includes(cameraCharacterizationStartingMessage)
  ) {
    characterizationLogs.unshift(cameraCharacterizationStartingMessage);
  }
  updateCameraAddLog(
    'characterization',
    characterizationLogs,
    status.result
  );

  cameraCharacterizationQuestion = status.question?.id || null;
  document.getElementById('camera-characterization-question').hidden = !status.question;
  document.getElementById('camera-characterization-prompt').textContent = status.question?.message || '';
  const confirmationButtons = document.querySelectorAll('#camera-characterization-question button');
  const beforeTest = status.question?.kind === 'start';
  confirmationButtons[0].textContent = beforeTest ? 'GO' : 'OUI';
  confirmationButtons[0].style.backgroundColor = '#198754';
  confirmationButtons[0].style.color = '#fff';
  confirmationButtons[1].textContent = 'NON';
  confirmationButtons[1].hidden = beforeTest;
  const questionPanel = document.getElementById('camera-characterization-question');
  questionPanel.style.display = status.question ? 'grid' : 'none';
  questionPanel.style.gridTemplateColumns = beforeTest ? '1fr' : '1fr 1fr';
  questionPanel.style.gap = '8px';
  document.getElementById('camera-characterization-prompt').style.gridColumn = '1 / -1';

  const resultStatus = status.result && status.result.status;
  if (!status.running && status.job_id && cameraQualificationLocator &&
      (resultStatus === 'SUCCESS' || resultStatus === 'PARTIAL') &&
      cameraQualificationAutoValidationJob !== status.job_id) {
    cameraQualificationAutoValidationJob = status.job_id;
    void startAutomaticCameraValidation(cameraQualificationLocator);
  }
}

async function cameraJsonResponse(response, context) {
  const contentType = response.headers.get('content-type') || '';
  const body = await response.text();
  if (!contentType.includes('application/json')) {
    const clean = body.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
    throw new Error(clean
      ? `${context}: HTTP ${response.status}: ${clean}`
      : `${context}: HTTP ${response.status}: empty non-JSON response`);
  }
  try {
    return JSON.parse(body);
  } catch (error) {
    throw new Error(`${context}: invalid JSON response (HTTP ${response.status}): ${error.message}`);
  }
}

async function pollCameraCharacterization() {
  if (cameraCharacterizationPolling) return;
  cameraCharacterizationPolling = true;
  try {
    const response = await fetch('/api/camera-characterization');
    const data = await cameraJsonResponse(response, 'Characterization status');
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    renderCameraCharacterizationStatus(data);
  } catch (error) {
    updateCameraAddLog(
      'characterization',
      [`Characterization status unavailable: ${error.message}`]
    );
  } finally {
    cameraCharacterizationPolling = false;
  }
}

async function characterizationRequest(action, payload = {}) {
  const response = await fetch(`/api/camera-characterization/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await cameraJsonResponse(response, `Camera characterization ${action}`);
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function startCameraCharacterization() {
  const button = document.getElementById('camera-characterization-start');
  const select = document.getElementById('camera-characterization-select');
  const locator = select ? select.value : '';
  const option = select && select.selectedOptions ? select.selectedOptions[0] : null;
  const characterized = Boolean(option && option.dataset.characterized === '1');
  if (!locator) {
    flash('Select a connected camera first.', 'red');
    return;
  }
  cameraQualificationLocator = locator;
  cameraQualificationAutoValidationJob = '';
  cameraAddLogState.characterization = [];
  cameraAddLogState.validation = [];
  cameraAddLogState.characterizationOffset = 0;
  cameraAddLogState.validationOffset = 0;
  cameraAddLogState.characterizationResult = '';
  renderCameraAddLog();

  cameraCharacterizationStarting = true;
  cameraCharacterizationStartingMessage = characterized
    ? 'Starting camera re-characterization…'
    : 'Starting camera characterization…';
  if (button) button.disabled = true;
  if (select) select.disabled = true;
  appendCameraAddLogLine('characterization', cameraCharacterizationStartingMessage);
  await waitForBrowserPaint();

  try {
    await characterizationRequest(characterized ? 'recharacterize' : 'start', {locator});
  } catch (error) {
    cameraCharacterizationStarting = false;
    cameraCharacterizationStartingMessage = '';
    if (select) select.disabled = false;
    if (button) button.disabled = false;
    appendCameraAddLogLine('characterization', `FAILED: ${error.message}`);
    flash(error.message, 'red');
    return;
  }
  cameraCharacterizationStarting = false;
  cameraCharacterizationStartingMessage = '';
}

async function cancelCameraQualification() {
  if (window.cameraValidationRunning) return cancelCameraValidation();
  return cancelCameraCharacterization();
}

async function cancelCameraCharacterization() {
  try {
    await characterizationRequest('cancel');
  } catch (error) {
    flash(error.message, 'red');
  }
}

async function answerCameraCharacterization(answer) {
  try {
    await characterizationRequest('answer', {
      question_id: cameraCharacterizationQuestion,
      answer
    });
  } catch (error) {
    flash(error.message, 'red');
  }
}

// ── AUDIO iOS-compatible ─────────────────────────────────────────────────────
// iOS Safari exige que l'AudioContext soit créé ET resume() dans un geste direct.
// On crée le contexte au premier geste, et on appelle resume() avant chaque son.

const SOUND_FILES = ['filters_on.wav','filters_off.wav','10minutes.wav','5minutes.wav',
                     '2minutes.wav','60seconds.wav','30seconds.wav','10seconds.wav',
                     '1.wav','2.wav','3.wav','4.wav','5.wav','contact.wav'];

function initAudio() {
  if (state.audioCtx) {
    // Déjà créé — s'assurer qu'il n'est pas suspendu (iOS remet en suspended)
    if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
    return;
  }
  state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  state.audioCtx.resume().then(() => {
    // Pre-cache tous les sons une fois le contexte actif
    SOUND_FILES.forEach(f => cacheSound(f));
  });
}

// Déclencher initAudio sur tout geste utilisateur
document.addEventListener('touchstart', initAudio, { passive: true });
document.addEventListener('touchend',   initAudio, { passive: true });
document.addEventListener('click',      initAudio);

async function cacheSound(filename) {
  if (!state.audioCtx || state.audioBuffers[filename]) return;
  try {
    const resp = await fetch(`/static/sounds/${filename}`);
    const buf  = await resp.arrayBuffer();
    state.audioBuffers[filename] = await state.audioCtx.decodeAudioData(buf);
  } catch(e) { console.warn('Cache sound failed:', filename, e); }
}

async function playSound(filename) {
  if (!state.soundsEnabled) return;

  // Créer le contexte si absent (premier appel depuis un geste)
  if (!state.audioCtx) initAudio();
  if (!state.audioCtx) return;

  // iOS suspend le contexte en arrière-plan — toujours resume() avant de jouer
  if (state.audioCtx.state !== 'running') {
    try { await state.audioCtx.resume(); } catch(e) {}
  }

  // Charger le son si pas encore en cache
  if (!state.audioBuffers[filename]) {
    await cacheSound(filename);
  }

  const buf = state.audioBuffers[filename];
  if (!buf) { console.warn('Sound unavailable:', filename); return; }

  try {
    if (state.currentSound) { try { state.currentSound.stop(); } catch(e){} }
    const src  = state.audioCtx.createBufferSource();
    const gain = state.audioCtx.createGain();
    gain.gain.value = state.volume;
    src.buffer = buf;
    src.connect(gain);
    gain.connect(state.audioCtx.destination);
    src.start(0);
    state.currentSound = src;
    // Highlight bouton test
    document.querySelectorAll('.sound-btn').forEach(b => {
      if (b.getAttribute('onclick') && b.getAttribute('onclick').includes(filename))
        b.classList.add('playing');
    });
    src.onended = () => {
      document.querySelectorAll('.sound-btn').forEach(b => b.classList.remove('playing'));
    };
  } catch(e) { console.error('playSound error:', e); }
}

// ════════════════════════════════════════════════════════════════
// SOCKETIO
// ════════════════════════════════════════════════════════════════
const socket = io({ transports: ['websocket'] });

socket.on('camera_characterization_status', status => {
  if (status && typeof status === 'object') {
    renderCameraCharacterizationStatus(status);
  }
});

socket.on('camera_validation_status', status => {
  if (status && typeof status === 'object') {
    renderCameraValidationStatus(status);
  }
});

socket.on('audio_play', data => {
  const filename = data && data.filename;

  if (
    state.soundsEnabled &&
    typeof filename === 'string' &&
    filename.toLowerCase().endsWith('.wav')
  ) {
    playSound(filename);
  }
});

socket.on('audio_enabled', data => {
  if (data && typeof data.enabled === 'boolean') {
    applySoundsEnabled(data.enabled);
  }
});

socket.on('audio_volume', data => {
  if (data && Number.isFinite(Number(data.volume))) {
    applyVolume(Number(data.volume) * 100);
  }
});

let _eclipseSavePrefix = null;
function updateEclipseSaveFilename(eclipseData) {
  if (!eclipseData) return;

  const eclipseDate = eclipseData._date || eclipseData._date_utc;
  if (typeof eclipseDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(eclipseDate)) {
    return;
  }

  _eclipseSavePrefix = `${eclipseDate.replace(/-/g, '')}_Circumstances_`;
}


function handleEclipseSelectionChange() {
  const select = document.getElementById('inp-eclipse');
  if (!select) return;

  const eclipseDate = select.value;

  if (/^\d{4}-\d{2}-\d{2}$/.test(eclipseDate)) {
    updateEclipseSaveFilename({_date: eclipseDate});
  }
}

async function loadSupportedEclipses() {
  const select = document.getElementById('inp-eclipse');
  if (!select) return;

  const previous = select.value || 'auto';

  try {
    const response = await fetch('/api/eclipse/supported');
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }

    const dates = Array.isArray(payload.dates) ? payload.dates : [];

    select.innerHTML = '<option value="auto">Auto (next)</option>';

    dates.forEach(date => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return;

      const option = document.createElement('option');
      option.value = date;
      option.textContent = date;
      select.appendChild(option);
    });

    if ([...select.options].some(option => option.value === previous)) {
      select.value = previous;
    } else {
      select.value = 'auto';
    }
  } catch (error) {
    console.warn('Unable to load eclipse list:', error);
  }
}

async function _reanchorClockFromStatus() {
  try {
    const response = await fetch('/api/status');
    if (response.ok) {
      const status = await response.json();
      const payload = status;
      updateTime(status.time);
      updateEclipseSaveFilename(status.eclipse);
      renderDevices(payload.devices || {});
      if (status.gps) updateGPS(status.gps);
      updateRigs(payload.rigs || DEFAULT_RIGS);

      // /api/status exposes configuration-only RIG summaries. Reload the
      // persisted device bindings after a full browser reload so Camera and
      // Trigger labels keep the assigned hardware.
      await loadRigDevices();
    }
  } catch (e) {
    console.warn('Unable to re-anchor time from status:', e);
  }
}

socket.on('connect', async () => {
  flash('Connected to Pi ✓', 'green');
  try {
    const response = await fetch('/api/status');
    if (response.ok) {
      const status = await response.json();
      const payload = status;
      updateTime(status.time);
      updateEclipseSaveFilename(status.eclipse);
      renderDevices(payload.devices || {});
      if (status.gps) updateGPS(status.gps);
      updateRigs(payload.rigs || DEFAULT_RIGS);

      // A new browser document has no cached device bindings. Rehydrate them
      // once from the persisted RIG configuration after Socket.IO attaches.
      await loadRigDevices();
startDeviceAutoRefresh();
    }
  } catch (e) {
    console.warn('Unable to re-anchor time after connection:', e);
  }
});
socket.io.on('reconnect', _reanchorClockFromStatus);
socket.on('disconnect', () => flash('Disconnected', 'red'));

socket.on('gps_sync_done', async d => {
  resetGpsActionButtons();

  const copyLocation = _pendingGpsLocationCopy;
  _pendingGpsLocationCopy = false;

  _updateGpsBadge(d && d.synced === true);

  // Toujours relire le snapshot GPS complet :
  // TIME, LOCATION et TIME+LOCATION doivent tous rafraîchir l'IHM.
  try {
    const response = await fetch('/api/gps/state');
    if (response.ok) {
      const gps = await response.json();
      updateGPS(gps);

      // Copie ponctuelle uniquement si CETTE action opérateur demandait
      // explicitement une acquisition de position.
      if (copyLocation && d && d.synced === true) {
        copyGpsLocationToEclipseForm(gps);
      }
    }
  } catch (e) {
    console.warn('Unable to refresh GPS state:', e);
  }

  // Si l'heure système a été modifiée, recaler également l'horloge Pi.
  if (d && d.synced === true) {
    _reanchorClockFromStatus();
  }
});

socket.on('clock_reset', d => {
  _reanchorClockFromReset(d);
});

socket.on('status_update', payload => {
  const d = payload;
  updateRigs(payload.rigs || DEFAULT_RIGS);
  if (d.time)    updateTime(d.time);
  if (d.gps)     updateGPS(d.gps);
  if (d.camera) updateCameraTimeSync(d.camera, d.gps || state.gps || {});
  if (d.trigger && d.trigger.rigs) {
    state.triggerRigs = d.trigger.rigs;
    updateSelectedTriggerPhase();
    restoreActiveTriggerInputs().catch(error => {
      console.warn('Unable to restore active Trigger inputs:', error);
    });
  }
  if (d.eclipse) updateEclipseSaveFilename(d.eclipse);
  // Restaurer le fichier config caméra sélectionné depuis l'état backend
  if (d.camera_config_file) {
    _triggerCameraConfigFile = d.camera_config_file;
    _restoreTriggerCameraSelect();
  }
  // Restauration éclipse si présente dans le status_update initial
  if (d.eclipse && !state.eclipse) {
    state.eclipse = d.eclipse;
    // Appliquer la timezone sauvegardée dans le JSON si présente
    if (d.eclipse._timezone) _gpsTimezone = d.eclipse._timezone;
    renderContacts(d.eclipse);
  }
});

socket.on('gps_update', d => {
  if (d.synced === true) {
    if (d.time) updateTime(d.time);
    else _reanchorClockFromStatus();
  }
  updateGPS(d);
});
function triggerPhaseRunningState(d, nextPhase) {
  if (typeof d.running === 'boolean') return d.running;
  return nextPhase !== 'idle' && nextPhase !== 'failed';
}

socket.on('trigger_phase', d => {
  const rigId = Number(d && d.rig_id);
  if (!Number.isInteger(rigId) || rigId < 1 || rigId > 4) return;

  const key = String(rigId);
  const nextPhase = d.phase || 'idle';

  // trigger_phase is authoritative for process activity.  Keeping only
  // `phase` here leaves the previous `running=true` value cached in the
  // browser after STOP, which keeps START/DRY-RUN/DEBUG disabled forever
  // until another full status_update happens.
  state.triggerRigs[key] = {
    ...(state.triggerRigs[key] || {}),
    phase: nextPhase,
    running: nextPhase !== 'idle',
  };
  state.triggerRigs[key].running = triggerPhaseRunningState(d, nextPhase);

  if (rigId === selectedTriggerRigId) {
    updateSelectedTriggerPhase();
  }
});

  socket.on("trigger_failure", (payload) => {
    const rigId = normalizeRigId(payload && payload.rig_id, selectedTriggerRigId);
    const code = payload && payload.code ? ` [${payload.code}]` : '';
    const message = payload && payload.message ? payload.message : 'Trigger process failed.';
    flash(`RIG ${rigId} — TRIGGER FAILED${code}: ${message}`, 'red');
  });

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) _reanchorClockFromStatus();
});


socket.on('eclipse_calculated', d => {
  // Réactiver le bouton dès réception du résultat
  const btn = document.getElementById('btn-calc');
  if (btn) { btn.disabled = false; btn.textContent = '🌑 Calculate contacts'; }
  if (d.status === 'success' && d.data) {
    // Appliquer la timezone DST AVANT renderContacts pour que les heures locales soient correctes
    if (d.timezone_override) {
      console.log('[DST] timezone_override received:', d.timezone_override);
      _gpsTimezone = d.timezone_override;
      const sysTz = document.getElementById('sys-timezone');
      if (sysTz) sysTz.textContent = d.timezone_override;
    } else {
      console.log('[DST] pas de timezone_override dans eclipse_calculated');
    }
    console.log('[DST] _gpsTimezone=', _gpsTimezone, 'offset=', _getTimezoneOffset());
    state.eclipse = d.data;
    updateEclipseSaveFilename(d.data);
    renderContacts(d.data);
    populateOverrides(d.data);
    flash('Calculation completed ✓', 'green');
  } else {
    flash('Calculation failed', 'red');
  }
});

socket.on('state_update', d => {
  if (d.rigs) updateRigs(d.rigs);
  if (d.devices) updateControlsVisibility(d.devices);
  // Mise à jour timezone DST calculé côté serveur
  if (d.timezone_override) {
    _gpsTimezone = d.timezone_override;
    // Mettre à jour l'affichage du fuseau dans l'UI
    const sysTz = document.getElementById('sys-timezone');
    if (sysTz) sysTz.textContent = d.timezone_override;
    // Recalculer les heures locales si on a déjà des contacts
    if (state.eclipse) renderContacts(state.eclipse);
  }
});

// Log ligne par ligne (temps réel)
socket.on('log_line', d => {
  if (d.source === 'trigger') {
    appendTriggerRigLog(d);
  } else {
    appendLog(d.text, d.level, d.source, d.timestamp);
  }

  if (d.source === 'calculator') {
    appendCalcLog(d.text, d.level);
  }
});

// Historique complet à la (re)connexion
socket.on('log_history', lines => {
  const sources = ['gps_sync', 'calculator', 'trigger'];

  sources.forEach(source => {
    let containerId = 'log-container';
    if (source === 'gps_sync') containerId = 'log-container-gps_sync';
    else if (source === 'calculator') containerId = 'log-container-calculator';
    else if (source === 'trigger') {
      for (let rigId = 1; rigId <= 4; rigId += 1) {
        triggerLogEntries[rigId] = [];
      }

      if (!_logPaused) {
        lines
          .filter(d => d.source === 'trigger')
          .forEach(d => {
            const rigId = triggerLogRigId(d);
            triggerLogEntries[rigId].push(d);
          });
      }

      renderTriggerLog();
      return;
    }

    const containers = document.querySelectorAll(`#${containerId}`);
    if (containers.length === 0) return;

    containers.forEach(c => {
      c.innerHTML = '';
      const sourceLines = lines.filter(d => d.source === source);
      sourceLines.forEach(d => {
        const div = document.createElement('div');
        div.className = `log-line ${d.level}`;
        div.textContent = d.timestamp ? `[${d.timestamp}] ${d.text}` : d.text;
        c.appendChild(div);
      });
      const sep = document.createElement('div');
      sep.style.cssText = 'border-top:1px dashed #1e3a5f;margin:4px 0;font-size:10px;color:#1e3a5f;text-align:center';
      sep.textContent = '── reconnexion ──';
      c.appendChild(sep);
      c.scrollTop = c.scrollHeight;
    });
  });
});
// FOCUSER UI START
(() => {
  const section = document.getElementById('focuser-section');
  section.style.removeProperty('display');
  const plugin = document.getElementById('focuser-plugin');
  const status = document.getElementById('focuser-status');
  const position = document.getElementById('focuser-position');
  const target = document.getElementById('focuser-target');
  const slowStep = document.getElementById('focuser-step-slow');
  const fastStep = document.getElementById('focuser-step-fast');
  const speedSwitch = document.getElementById('focuser-speed-switch');
  const goButton = document.getElementById('btn-focuser-go');
  const homeButton = document.getElementById('btn-focuser-home');
  const directionButtons = document.querySelectorAll('[data-focuser-direction]');
  let active = false;
  let absoluteMotion = null;
  let press = null;
  let pollTimer = null;

  function focuserUrl(path) {
    const rig = selectedControlsRig();
    const focuser = rig && rig.devices && rig.devices.focuser;
    const backend = focuser && (focuser.backend || focuser.plugin);
    return focuser && ![null, '', 'none'].includes(backend)
      ? `/api/rigs/${rig.rig_id}/focuser/${path}`
      : null;
  }

  function displayFocuser(data) {
    if (!data || !active) return;
    plugin.textContent = data.plugin || plugin.textContent || '--';
    status.textContent = data.state || (data.moving ? 'moving' : (data.connected ? 'ready' : 'disconnected'));
    position.textContent = Number.isFinite(data.position) ? data.position : '--';
    if (Number.isInteger(data.step_fine) && data.step_fine > 0) slowStep.value = data.step_fine;
    if (Number.isInteger(data.step_coarse) && data.step_coarse > 0) fastStep.value = data.step_coarse;
    if (data.mode === 'slow' || data.mode === 'fast') {
      speedSwitch.checked = data.mode === 'fast';
    }
    absoluteMotion = (data.motion_command === 'go' || data.motion_command === 'home')
      ? data.motion_command
      : null;
    const selectedRig = selectedControlsRig();
    const triggerState = selectedRig
      ? (state.triggerRigs[String(selectedRig.rig_id)] || {})
      : {};
    const controlsEnabled = active && triggerState.running !== true;
    [goButton, homeButton].forEach(button => {
      const isCancel = button.dataset.focuserAction === absoluteMotion;
      button.textContent = isCancel ? 'Cancel' : (button.dataset.focuserAction === 'go' ? 'Go' : 'Home');
      button.classList.toggle('focuser-cancel', isCancel);
      button.disabled = absoluteMotion ? !isCancel : !controlsEnabled;
    });
    const disableOtherControls = Boolean(absoluteMotion) || !controlsEnabled;
    directionButtons.forEach(button => { button.disabled = disableOtherControls; });
    slowStep.disabled = disableOtherControls;
    fastStep.disabled = disableOtherControls;
    speedSwitch.disabled = disableOtherControls;
    schedulePoll(data.moving === true ? 400 : 1500);
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP error ${response.status}`);
    return data;
  }

  async function refreshFocuser() {
    const url = focuserUrl('status');
    if (!active || !url) return;
    try {
      displayFocuser(await request(url));
    } catch (error) {
      schedulePoll(1500);
    }
  }

  function schedulePoll(delay) {
    clearTimeout(pollTimer);
    if (active) pollTimer = setTimeout(refreshFocuser, delay);
  }

  function applyDevices(devices) {
    const focuser = devices && devices.focuser;
    active = Boolean(focuserUrl('status'));
    if (!active) {
      clearTimeout(pollTimer);
      stopPress(false);
      return;
    }
    const rigFocuser = selectedControlsRig().devices.focuser;
    plugin.textContent = rigFocuser.backend || rigFocuser.plugin || (focuser && focuser.plugin) || '--';
    refreshFocuser();
  }

  const renderDevicesWithoutFocuser = renderDevices;
  renderDevices = function(devices) {
    renderDevicesWithoutFocuser(devices);
    applyDevices(devices);
  };

  function post(path, body, renderResponse = true) {
    const url = focuserUrl(path);
    if (!url) return Promise.resolve(null);
    return request(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    }).then(data => {
      if (renderResponse) displayFocuser(data);
      return data;
    }).catch(error => flash(`Focuser : ${error.message}`, 'red'));
  }

  function cancelAbsoluteMotion() {
    post('stop');
  }

  homeButton.addEventListener('click', () => {
    if (absoluteMotion === 'home') cancelAbsoluteMotion();
    else post('home');
  });
  goButton.addEventListener('click', () => {
    if (absoluteMotion === 'go') {
      cancelAbsoluteMotion();
      return;
    }
    const requestedPosition = Number.parseInt(target.value, 10);
    if (Number.isInteger(requestedPosition)) post('move_to', {position: requestedPosition});
  });

  function saveSteps() {
    const fine = Number.parseInt(slowStep.value, 10);
    const coarse = Number.parseInt(fastStep.value, 10);
    if (fine > 0 && coarse > 0) post('set_step', {fine, coarse});
  }
  slowStep.addEventListener('change', saveSteps);
  fastStep.addEventListener('change', saveSteps);
  speedSwitch.addEventListener('change', () => {
    post('mode', {mode: speedSwitch.checked ? 'fast' : 'slow'}, false)
      .then(data => {
        if (data) displayFocuser(data);
      });
  });

  function beginPress(event, sign) {
    if (!active || press) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    press = {pointerId: event.pointerId, sign, jogStarted: false, stopSent: false};
    press.timer = setTimeout(() => {
      if (!press || press.pointerId !== event.pointerId) return;
      press.jogStarted = true;
      post('jog/start', {
        direction: sign < 0 ? 'decrease' : 'increase',
      });
    }, 400);
  }

  function stopPress(singleStep) {
    if (!press) return;
    const ended = press;
    press = null;
    clearTimeout(ended.timer);
    if (ended.jogStarted) {
      if (!ended.stopSent) {
        ended.stopSent = true;
        const url = focuserUrl('jog/stop');
        if (url) fetch(url, {method: 'POST'}).catch(() => {});
      }
    } else if (singleStep && active) {
      post('step', {direction: ended.sign < 0 ? 'decrease' : 'increase'});
    }
  }

  [
    [document.getElementById('btn-focuser-minus'), -1],
    [document.getElementById('btn-focuser-plus'), 1],
  ].forEach(([button, sign]) => {
    button.addEventListener('pointerdown', event => beginPress(event, sign));
    button.addEventListener('pointerup', () => stopPress(true));
    button.addEventListener('pointercancel', () => stopPress(false));
    button.addEventListener('pointerleave', event => {
      if (press && press.pointerId === event.pointerId) stopPress(false);
    });
  });

  window.addEventListener('blur', () => stopPress(false));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopPress(false);
  });
  window.addEventListener('unload', () => stopPress(false));

  document.addEventListener('controlsrigchange', () => {
    active = Boolean(focuserUrl('status'));
    if (!active) {
      clearTimeout(pollTimer);
      stopPress(false);
    }
  });

  socket.on('focuser_update', refreshFocuser);
  socket.on('status_update', data => {
    if (data.devices) {
      const devices = data.devices;
      updateControlsVisibility(devices);
      applyDevices(devices);
    }
    if (data.focuser) refreshFocuser();
  });
})();
// FOCUSER UI END

// MOUNT UI START
(() => {
  const homeButton = document.getElementById('btn-mount-home');
  const slewSpeed = document.getElementById('mount-slew-speed');
  const slewSpeedValue = document.getElementById('mount-slew-speed-value');
  const trackingMode = document.getElementById('mount-tracking-mode');
  const trackingSwitch = document.getElementById('mount-tracking-switch');
  const slewButtons = Array.from(document.querySelectorAll('.mount-slew-button'));
  let homing = false;
  let trackingEnabled = false;
  let trackingCommandPending = false;

  function selectedMountTriggerRunning() {
    const rig = selectedControlsRig();
    if (!rig) return false;

    const triggerState = state.triggerRigs[String(rig.rig_id)] || {};
    return triggerState.running === true;
  }

  let triggerRunning = selectedMountTriggerRunning();
  let pollTimer = null;
  let slewSpeedValues = null;
  let activeSlew = null;
  let slewGestureSequence = 0;

  function mountUrl(path) {
    const rig = selectedPilotableMountRig();
    return rig ? `/api/rigs/${rig.rig_id}/mount/${path}` : null;
  }

  function disableMountControls() {
    homeButton.disabled = true;
    slewSpeed.disabled = true;
    trackingMode.disabled = true;
    trackingSwitch.disabled = true;
    slewButtons.forEach(button => { button.disabled = true; });
  }

  function selectedSlewSpeed() {
    return slewSpeedValues
      ? slewSpeedValues[Number(slewSpeed.value)].value
      : Number(slewSpeed.value);
  }

  function displaySlewSpeedValue() {
    if (slewSpeedValues) {
      const selected = slewSpeedValues[Number(slewSpeed.value)];
      slewSpeedValue.textContent = selected
        ? `${selected.label ?? selected.value}${slewSpeed.dataset.unit || ''}`
        : '';
      return;
    }
    slewSpeedValue.textContent = `${slewSpeed.value}${slewSpeed.dataset.unit || ''}`;
  }

  function displayMount(data) {
    if (!selectedPilotableMountRig()) {
      disableMountControls();
      return;
    }
    homing = data && data.homing === true;
    homeButton.disabled = false;
    homeButton.textContent = homing ? 'STOP' : 'HOME';
    homeButton.classList.toggle('focuser-cancel', homing);
    slewButtons.forEach(button => { button.disabled = homing; });
    if (data && typeof data.trigger_running === 'boolean') {
      triggerRunning = data.trigger_running;
    }

    const slewSpeedCaps = data && data.slew_speed_caps;
    slewSpeedValues = slewSpeedCaps && slewSpeedCaps.kind === 'discrete'
      && Array.isArray(slewSpeedCaps.values)
      ? slewSpeedCaps.values
      : null;
    if (slewSpeedValues && slewSpeedValues.length > 0) {
      slewSpeed.min = 0;
      slewSpeed.max = slewSpeedValues.length - 1;
      slewSpeed.step = 1;
      const selectedIndex = slewSpeedValues.findIndex(item => item.value === data.slew_speed);
      if (selectedIndex >= 0) slewSpeed.value = selectedIndex;
      slewSpeed.dataset.unit = slewSpeedCaps.unit || '';
    } else if (slewSpeedCaps && slewSpeedCaps.kind === 'range') {
      slewSpeed.min = slewSpeedCaps.min;
      slewSpeed.max = slewSpeedCaps.max;
      slewSpeed.step = slewSpeedCaps.step;
      slewSpeed.value = data.slew_speed;
      slewSpeed.dataset.unit = slewSpeedCaps.unit || '';
    }
    slewSpeed.disabled = triggerRunning || !slewSpeedCaps
      || (slewSpeedValues && slewSpeedValues.length === 0);
    displaySlewSpeedValue();

    const capabilities = data && data.tracking_caps;
    const modes = capabilities && Array.isArray(capabilities.modes)
      ? capabilities.modes
      : [];
    trackingMode.replaceChildren(...modes.map(mode => {
      const option = document.createElement('option');
      option.value = mode;
      option.textContent = mode;
      return option;
    }));
    trackingMode.value = data && data.tracking_mode;
    trackingEnabled = data && data.tracking_enabled === true;
    trackingSwitch.checked = trackingEnabled;

    trackingMode.disabled = triggerRunning || modes.length === 0;
    trackingSwitch.disabled = (
      triggerRunning
      || trackingCommandPending
      || !capabilities
      || capabilities.toggle !== true
    );
    scheduleMountRefresh(homing ? 400 : 1500);
  }

  async function refreshMount() {
    const url = mountUrl('status');
    if (!url) {
      clearTimeout(pollTimer);
      disableMountControls();
      return;
    }
    try {
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP error ${response.status}`);
      if (url !== mountUrl('status')) return;
      displayMount(data);
    } catch (error) {
      scheduleMountRefresh(1500);
    }
  }

  function scheduleMountRefresh(delay) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refreshMount, delay);
  }

  async function postMount(url, options = {method: 'POST'}) {
    if (!url) return;
    try {
      const response = await fetch(url, options);
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || `HTTP error ${response.status}`);
      }
    } catch (error) {
      flash(`Mount : ${error.message}`, 'red');
    }
    refreshMount();
  }

  function sendSlewStop(slew) {
    if (!slew || !slew.stopUrl) return Promise.resolve();
    return fetch(slew.stopUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({gesture_id: slew.gestureId}),
    }).catch(() => {});
  }

  function finalizeReleasedSlew(slew) {
    if (!slew || !slew.releaseRequested) return;
    sendSlewStop(slew).finally(() => {
      if (activeSlew === slew) activeSlew = null;
    });
  }

  function stopSlewBestEffort() {
    const slew = activeSlew;
    if (!slew || slew.releaseRequested) return;

    slew.releaseRequested = true;

    // Send one STOP immediately so the server can mark the gesture released
    // even if this request reaches it before START.
    sendSlewStop(slew);

    // Send another STOP only after START has settled.  This closes the
    // short-press race where independent HTTP requests are reordered.
    Promise.resolve(slew.startPromise).finally(() => {
      finalizeReleasedSlew(slew);
    });
  }

  function startSlew(event) {
    const rig = selectedPilotableMountRig();
    if (!rig || homing || activeSlew) return;

    const rigId = Number(rig.rig_id);
    const startUrl = `/api/rigs/${rigId}/mount/slew/start`;
    const stopUrl = `/api/rigs/${rigId}/mount/slew/stop`;
    event.preventDefault();

    const button = event.currentTarget;
    const gestureId = `${Date.now()}-${++slewGestureSequence}`;
    const slew = {
      button,
      pointerId: event.pointerId,
      rigId,
      stopUrl,
      gestureId,
      releaseRequested: false,
      startPromise: null,
    };
    activeSlew = slew;
    button.setPointerCapture(event.pointerId);

    slew.startPromise = fetch(startUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        direction: button.dataset.direction,
        gesture_id: gestureId,
      }),
    }).catch(() => null);
  }

  slewButtons.forEach(button => {
    button.addEventListener('pointerdown', startSlew);
    button.addEventListener('pointerup', stopSlewBestEffort);
    button.addEventListener('pointercancel', stopSlewBestEffort);
    button.addEventListener('lostpointercapture', stopSlewBestEffort);
    button.addEventListener('dragstart', event => event.preventDefault());
  });
  window.addEventListener('pointerup', stopSlewBestEffort, true);
  window.addEventListener('pointercancel', stopSlewBestEffort, true);
  window.addEventListener('blur', stopSlewBestEffort);
  window.addEventListener('pagehide', stopSlewBestEffort);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopSlewBestEffort();
  });

  homeButton.addEventListener('click', () => {
    postMount(mountUrl(homing ? 'slew/stop' : 'home'));
  });

  slewSpeed.addEventListener('input', displaySlewSpeedValue);
  slewSpeed.addEventListener('change', () => {
    postMount(mountUrl('speed'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({speed: selectedSlewSpeed()}),
    });
  });

  trackingMode.addEventListener('change', () => {
    postMount(mountUrl('tracking/mode'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: trackingMode.value}),
    });
  });

  trackingSwitch.addEventListener('change', async () => {
    const requestedTracking = trackingSwitch.checked;

    // A checkbox changes visually before the "change" handler runs.
    // Restore the last authoritative mount state immediately. The switch
    // becomes green only when /status confirms tracking_enabled=true.
    trackingSwitch.checked = trackingEnabled;
    trackingCommandPending = true;
    trackingSwitch.disabled = true;

    try {
      await postMount(mountUrl(requestedTracking
        ? 'tracking/start'
        : 'tracking/stop'));
    } finally {
      trackingCommandPending = false;
      await refreshMount();
    }
  });

  document.addEventListener('controlsrigchange', () => {
    const rig = selectedPilotableMountRig();
    const selectedRigId = rig ? Number(rig.rig_id) : null;

    // renderControlsRigSelection() also emits controlsrigchange during normal
    // UI refreshes.  A refresh of the SAME RIG must never stop a held slew.
    if (activeSlew && activeSlew.rigId !== selectedRigId) {
      stopSlewBestEffort();
    }

    triggerRunning = selectedMountTriggerRunning();
    refreshMount();
  });
  socket.on('connect', refreshMount);
  socket.on('status_update', refreshMount);
  socket.on('trigger_phase', data => {
    const rigId = Number(data && data.rig_id);
    const rig = selectedControlsRig();

    if (!rig || Number(rig.rig_id) !== rigId) return;

    triggerRunning = data.phase !== 'idle';
    refreshMount();
  });
  refreshMount();
})();
// MOUNT UI END

// ════════════════════════════════════════════════════════════════
// HORLOGE — démarre après le premier recalage sur le Pi
// ════════════════════════════════════════════════════════════════
// Ancrage recalé à chaque status_update reçu du Pi.
let _clockAnchorEpochMs = null;
let _clockAnchorUtcMs = null;
let _clockAnchorLocalMs = null;
let _clockAnchorPerfMs = null;
let _clockInterval = null;
let _clockSource = 'Pi';
let _gpsTimezone = null;   // timezone reçue du GPS — prioritaire sur le navigateur

// Retourne l'offset timezone en heures, dans cet ordre de priorité :
// 1. GPS synchronisé (_gpsTimezone ou state.gps.timezone)
// 2. Champ Fuseau UTC+ saisi manuellement dans le formulaire (inp-tz)
// 3. null si rien de disponible
function _getTimezoneOffset() {
  const tzValue = _gpsTimezone ?? (state.gps && state.gps.timezone);
  if (typeof tzValue === 'number' && Number.isFinite(tzValue)) return tzValue;
  if (typeof tzValue === 'string' && tzValue) {
    const m = tzValue.match(/UTC([+-]?\d+(?:\.\d+)?)/);
    if (m) return parseFloat(m[1]);
  }
  // Fallback : champ Fuseau UTC+ du formulaire
  const inpTz = document.getElementById('inp-tz');
  if (inpTz && inpTz.value !== '' && inpTz.value !== null) {
    const v = parseFloat(inpTz.value);
    if (!isNaN(v)) return v;
  }
  return null;
}

// Convertit une heure UTC "HH:MM:SS" en heure locale.
// Priorité : timezone GPS > champ Fuseau UTC+ > UTC inchangé.
function _utcToLocal(utcHms) {
  if (!utcHms || utcHms === '--') return utcHms;
  const offsetH = _getTimezoneOffset();
  if (offsetH === null) return utcHms;
  const sec = _toSec(utcHms);
  if (sec === null) return utcHms;
  return _fromSec(sec + offsetH * 3600);
}

function _isoUtcToConfiguredLocalTime(iso) {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return '';
  const offsetH = _getTimezoneOffset() ?? 0;
  const d = new Date(ms + offsetH * 3600000);
  return `${fmt(d.getUTCHours())}:${fmt(d.getUTCMinutes())}:${String(d.getUTCSeconds()+d.getUTCMilliseconds()/1000).padStart(6,'0')}`;
}

function _nowAdjusted() {
  // The Pi is the sole time authority. performance.now() is monotonic and is
  // immune to iPad/browser wall-clock or timezone changes after reconnection.
  const utcMs = Number.isFinite(_clockAnchorUtcMs)
    ? _nowAdjustedUtcMs()
    : _clockAnchorEpochMs + (performance.now() - _clockAnchorPerfMs);
  return new Date(utcMs);
}

function _nowAdjustedUtcMs() {
  return _clockAnchorUtcMs + (performance.now() - _clockAnchorPerfMs);
}

function _nowAdjustedLocalMs() {
  // L'epoch Unix est toujours un instant absolu : UTC et local ont donc
  // le même epoch. Pour afficher l'heure locale du Trigger, appliquer
  // explicitement l'offset configuré/GPS, jamais celui du navigateur.
  const offsetH = _getTimezoneOffset();
  if (!Number.isFinite(_clockAnchorUtcMs) || offsetH === null) return null;
  return _nowAdjustedUtcMs() + offsetH * 3600000;
}

function _tickClock() {
  if (!Number.isFinite(_clockAnchorUtcMs) || !Number.isFinite(_clockAnchorPerfMs)) return;
  const now = _nowAdjusted();
  const utcH = fmt(now.getUTCHours()), utcM = fmt(now.getUTCMinutes()), utcS = fmt(now.getUTCSeconds());
  const utcTime = `${utcH}:${utcM}:${utcS}`;
  const utcDate = now.toISOString().slice(0,10);
  const localMs = _nowAdjustedLocalMs();
  const hasLocalAnchor = Number.isFinite(localMs);
  const localDateObj = hasLocalAnchor ? new Date(localMs) : null;
  const locTime = localDateObj
    ? `${fmt(localDateObj.getUTCHours())}:${fmt(localDateObj.getUTCMinutes())}:${fmt(localDateObj.getUTCSeconds())}`
    : '--:--:--';
  const locDate = localDateObj ? localDateObj.toISOString().slice(0,10) : '---- -- --';

  // Header
  const hdrLocal = document.getElementById('hdr-local');
  const hdrUtc   = document.getElementById('hdr-utc');
  if (hdrLocal) {
    hdrLocal.textContent = locTime;
    hdrLocal.style.visibility = hasLocalAnchor ? 'visible' : 'hidden';
  }
  if (hdrUtc)   hdrUtc.textContent   = utcTime;

// Onglet Statut
const sysLocal = document.getElementById('sys-time-local');
const sysUtc   = document.getElementById('sys-time-utc');
const sysDateL = document.getElementById('sys-date-local');
const sysDateU = document.getElementById('sys-date-utc');
const sysTz    = document.getElementById('sys-timezone');

if (sysLocal) sysLocal.textContent = locTime;
if (sysUtc)   sysUtc.textContent   = utcTime;
if (sysDateL) sysDateL.textContent = locDate;
if (sysDateU) sysDateU.textContent = utcDate;
// Timezone display is backend/config-driven only. Never infer from the iPad.
if (sysTz && !_gpsTimezone && _getTimezoneOffset() === null) {
  sysTz.textContent = 'UTC';
}
}

function _updateGpsBadge(synced) {
  const badge = document.getElementById('hdr-gps-badge');
  if (!badge) return;
  if (synced) {
    badge.textContent = 'GPS SYNC';
    badge.style.background   = 'rgba(61,220,132,.15)';
    badge.style.color        = 'var(--green)';
    badge.style.borderColor  = 'rgba(61,220,132,.4)';
  } else {
    badge.textContent = 'NO SYNC';
    badge.style.background  = 'rgba(90,122,154,.2)';
    badge.style.color       = 'var(--text-dim)';
    badge.style.borderColor = 'var(--border)';
  }
}

function updateTime(t) {
  if (!t) return;
  let piMs = null;
  if (Number.isFinite(t.backend_utc_epoch_ms)) {
    piMs = t.backend_utc_epoch_ms;
  } else if (Number.isFinite(t.epoch_ms)) {
    piMs = t.epoch_ms;
  }
  if (Number.isFinite(piMs)) {
    let piLocalMs = null;
    if (Number.isFinite(t.backend_local_epoch_ms)) {
      piLocalMs = t.backend_local_epoch_ms;
    } else if (t.local && typeof t.local.iso === 'string'
               && /(?:Z|[+-]\d{2}:\d{2})$/i.test(t.local.iso)) {
      piLocalMs = Date.parse(t.local.iso.replace(/(?:Z|[+-]\d{2}:\d{2})$/i, 'Z'));
      if (!Number.isFinite(piLocalMs)) piLocalMs = null;
    }
    _clockAnchorEpochMs = piMs;
    _clockAnchorUtcMs = piMs;
    _clockAnchorLocalMs = piLocalMs;
    _clockAnchorPerfMs = performance.now();
    _clockSource = 'Pi';
    _tickClock();
    if (_clockInterval === null) {
      _clockInterval = setInterval(_tickClock, 1000);
    }
  }
}

function _reanchorClockFromReset(payload) {
  if (!payload
      || !Number.isFinite(payload.new_utc_epoch_ms)
      || !Number.isFinite(payload.new_local_epoch_ms)) return;
  updateTime({
    backend_utc_epoch_ms: payload.new_utc_epoch_ms,
    backend_local_epoch_ms: payload.new_local_epoch_ms,
  });
}

function updateGPS(gps) {
  state.gps = gps;
  const icon   = document.getElementById('gps-icon');
  const status = document.getElementById('gps-status-text');
  const dotGps = document.getElementById('dot-gps');

  const deviceDetected = state.gpsDeviceDetected === true;

  if (gps.connected && gps.synced) {
    if (icon) icon.textContent = '🟢';
    if (status) status.textContent = 'Connected — synchronized ✓';
    if (dotGps) dotGps.className = 'dot on';
  } else if (gps.connected) {
    if (icon) icon.textContent = '🟡';
    if (status) status.textContent = 'Connected — waiting for fix…';
    if (dotGps) dotGps.className = 'dot warn';
  } else if (gps.synced) {
    const t = gps.sync_time ? _isoUtcToConfiguredLocalTime(gps.sync_time) : '';
    if (icon) icon.textContent = '🟢';
    if (status) status.textContent = `Synchronized ✓${t ? ' — ' + t : ''}`;
    if (dotGps) dotGps.className = 'dot on';
  } else if (deviceDetected) {
    if (icon) icon.textContent = '🟡';
    if (status) status.textContent = 'Detected — not synchronized';
    if (dotGps) dotGps.className = 'dot warn';
  } else {
    if (icon) icon.textContent = '⚫';
    if (status) status.textContent = 'Not detected';
    if (dotGps) dotGps.className = 'dot off';
  }

  // Badge GPS sync dans le header
  _updateGpsBadge(gps.synced);

  const gpsLat = document.getElementById('gps-lat');
  const gpsLon = document.getElementById('gps-lon');
  const gpsAlt = document.getElementById('gps-alt');

  if (gpsLat) gpsLat.textContent = gps.lat != null ? Number(gps.lat).toFixed(5) : '--';
  if (gpsLon) gpsLon.textContent = gps.lon != null ? Number(gps.lon).toFixed(5) : '--';
  if (gpsAlt) gpsAlt.textContent = gps.alt != null ? Number(gps.alt).toFixed(0) + ' m' : '--';

  // Mettre à jour le timezone si disponible
  const sysTz = document.getElementById('sys-timezone');
  if (sysTz && gps.timezone) {
    _gpsTimezone = gps.timezone;
    sysTz.textContent = gps.timezone;
  }

  // Indicateur de synchro GPS dans l'horloge système
  const syncIndicator = document.getElementById('clock-gps-sync');
  if (syncIndicator) {
    if (gps.synced) {
      const t = gps.sync_time ? _isoUtcToConfiguredLocalTime(gps.sync_time) : '';
      syncIndicator.textContent = `⏱ GPS time synchronized${t ? ' at ' + t : ''}`;
      syncIndicator.style.color = 'var(--green)';
    } else {
      syncIndicator.textContent = '⏱ Time not synchronized (browser)';
      syncIndicator.style.color = 'var(--text-dim)';
    }
  }

  // IMPORTANT :
  // updateGPS() est appelé périodiquement par status_update/gps_update.
  // Il ne doit jamais écraser les champs éditables de préparation d'éclipse.
}

const PHASE_LABELS = {
  recovering: '↻ RECOVERING',
  failed: '⚠ TRIGGER FAILED',
  idle:         'IDLE',
  waiting:      'WAITING',
  partial:      'PARTIAL',
  diamond_ring: '💎 DIAMOND RING',
  totality:     '🌑 TOTALITY',
  partial_end:  'PARTIAL END',
};

function updateSelectedTriggerPhase() {
  const key = String(selectedTriggerRigId || '');
  const rigState = state.triggerRigs[key] || {};
  updatePhase(rigState.phase || 'idle');
}

function anyActiveTriggerRunning() {
  return activeTriggerRigIds().some(rigId => {
    const rigState = state.triggerRigs[String(rigId)] || {};

    return (
      rigState.running === true ||
      (
        rigState.phase &&
        rigState.phase !== 'idle'
      )
    );
  });
}


function updatePhase(phase) {
  state.phase = phase;
  const badge = document.getElementById('phase-badge');
  const ring  = document.getElementById('totality-ring');
  const label = document.getElementById('ring-label');
  const dot   = document.getElementById('dot-trigger');

  if (badge) { badge.className   = `phase-badge phase-${phase}`; badge.textContent = PHASE_LABELS[phase] || phase.toUpperCase(); }
  if (label) { label.textContent = PHASE_LABELS[phase] || phase; }
  if (ring)  { ring.classList.toggle('active', phase === 'totality' || phase === 'diamond_ring'); }
  if (dot)   { dot.className = phase !== 'idle' ? 'dot on' : 'dot off'; }

  const btnStart     = document.getElementById('btn-start');
  const btnDryRun    = document.getElementById('btn-dryrun');
  const btnDebug     = document.getElementById('btn-debug');
  const btnStop      = document.getElementById('btn-stop');
  const btnTot       = document.getElementById('btn-totality-only');

  const triggerStartLocked = anyActiveTriggerRunning();

  // START / DRY-RUN / DEBUG are global multi-RIG actions.
  if (btnStart)  btnStart.disabled  = triggerStartLocked;
  if (btnDryRun) btnDryRun.disabled = triggerStartLocked;
  if (btnDebug)  btnDebug.disabled  = triggerStartLocked;

  // STOP / Totality override remain targeted at the selected RIG only.
  const selectedRigState =
    state.triggerRigs[String(selectedTriggerRigId)] || {};
  const selectedRigRunning =
    selectedRigState.running === true ||
    (
      selectedRigState.phase &&
      selectedRigState.phase !== 'idle'
    );

  if (btnStop) {
    const stopPending = Boolean(
      window._triggerStopPendingRigs &&
      window._triggerStopPendingRigs.has(selectedTriggerRigId)
    );
    const selectedRigStopping = selectedRigState.phase === 'stopping';
    btnStop.disabled = !selectedRigRunning || stopPending;
    btnStop.textContent = stopPending
      ? (selectedRigStopping ? '⏳ Force stopping…' : '⏳ Stopping…')
      : (selectedRigStopping ? '■ FORCE STOP' : '■ STOP');
  }

  if (btnTot) {
    btnTot.style.opacity = '1';
    btnTot.disabled = false;
  }
}

// ════════════════════════════════════════════════════════════════
// GRILLE VITESSES TOTALITÉ (15 champs)
// ════════════════════════════════════════════════════════════════
const DEFAULT_SPEEDS = ["1/4000","1/2000","1/1000","1/500","1/250","1/125","1/60","1/30","1/15","1/8","1/4","1/2","1","2","4"];

function initSpeedsGrid(speeds) {
  const grid = document.getElementById('totality-speeds-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const vals = speeds || DEFAULT_SPEEDS;
  vals.forEach((v, i) => {
    const cell = document.createElement('div');
    cell.className = 'speed-cell';
    cell.onclick = () => { const cb = cell.querySelector('input'); cb.checked = !cb.checked; updateSpeedsSelection(); };
    cell.innerHTML = `<input type="checkbox" id="spd-${i}" checked onclick="event.stopPropagation();updateSpeedsSelection()"><label for="spd-${i}">${v}</label>`;
    grid.appendChild(cell);
  });
}

function updateSpeedsSelection() {
  // Cette fonction peut être utilisée pour mettre à jour l'affichage
  // ou pour sauvegarder la sélection si nécessaire
  const checkboxes = document.querySelectorAll('#totality-speeds-grid input[type="checkbox"]');
  const selected = Array.from(checkboxes)
    .filter(cb => cb.checked)
    .map(cb => DEFAULT_SPEEDS[parseInt(cb.id.split('-')[1])]);
  console.log('Selected shutter speeds:', selected);
}

function getSpeedsFromGrid() {
  const speeds = [];
  for (let i = 0; i < 20; i++) {
    const el = document.getElementById(`spd-${i}`);
    if (!el) break;
    const v = el.value.trim();
    if (v !== '') speeds.push(parseFloat(v));
  }
  return speeds;
}

// ════════════════════════════════════════════════════════════════
// ════════════════════════════════════════════════════════════════
// TMAX — règle : Totale → C2 + (C3-C2)/2 | Partielle → C1 + (C4-C1)/2
// ════════════════════════════════════════════════════════════════
function _toSec(hms) {
  try { const [h,m,s] = hms.split(':').map(Number); return h*3600+m*60+(s||0); } catch(e) { return null; }
}
function _fromSec(s) {
  s = ((s % 86400) + 86400) % 86400;
  let ms = Math.round(s * 1000) % 86400000;
  const h = Math.floor(ms/3600000); ms -= h*3600000;
  const m = Math.floor(ms/60000); ms -= m*60000;
  const sec = ms/1000;
  return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+sec.toFixed(3).padStart(6,'0');
}

// Calcule TMAX à partir d'un objet {C1, C2, C3, C4} (clés UTC HH:MM:SS)
// Détecte partielle si C2 == C3 ou si C2/C3 absents
function _calcTmax(c1, c2, c3, c4) {
  const midpoint = (a, b) => {
    if (a === null || b === null) return null;
    if (b < a) b += 86400; // passage minuit
    return _fromSec(a + (b - a) / 2);
  };
  const isPartial = !c2 || !c3 || c2 === c3;
  if (isPartial && c1 && c4) return midpoint(_toSec(c1), _toSec(c4));
  if (c2 && c3) return midpoint(_toSec(c2), _toSec(c3));
  return null;
}

document.addEventListener('DOMContentLoaded', () => {
  // Forcer ISO à 100 par défaut (le selected HTML n'est pas toujours respecté sur éléments cachés)
  document.getElementById('cfg-partial-iso').value = '100';
  document.getElementById('cfg-dr-iso').value      = '100';
  document.getElementById('cfg-tot-iso').value     = '100';
});
function renderContacts(data) {
  document.getElementById('contacts-card')?.style && (document.getElementById('contacts-card').style.display = '');
  document.getElementById('eclipse-duration') && (document.getElementById('eclipse-duration').textContent = data._duration || '--');

  // Appliquer la timezone sauvegardée dans le JSON (DST éclipse) pour _utcToLocal
  // Si absent, réinitialiser pour rester cohérent avec l'horloge header
  if (data._timezone) {
    _gpsTimezone = data._timezone;
  } else {
    _gpsTimezone = null;
  }

  // Helpers couleur/icône selon type
  const _displayEclipseType = (typeStr) => {
    if (!typeStr) return typeStr;

    const translations = {
      totale: 'Total',
      total: 'Total',
      partielle: 'Partial',
      partial: 'Partial',
      annulaire: 'Annular',
      annular: 'Annular',
      hybride: 'Hybrid',
      hybrid: 'Hybrid'
    };

    return translations[String(typeStr).toLowerCase()] || typeStr;
  };

  const _typeStyle = (typeStr) => {
    const t = (typeStr || '').toLowerCase();
    const colorMap = { totale: 'var(--green)', partielle: 'var(--yellow)', annulaire: 'var(--orange)' };
    const iconMap  = { totale: '🌑', partielle: '🌒', annulaire: '💍' };
    return {
      color: Object.entries(colorMap).find(([k]) => t.includes(k))?.[1] || 'var(--text-dim)',
      icon:  Object.entries(iconMap).find(([k])  => t.includes(k))?.[1] || '🌙',
    };
  };

  // TYPE GLOBAL (éclipse dans la bande) — _type_global ou extrait du label
  const typeGlobal = data._type_global
    || (data._eclipse || data.title || '').match(/(Totale|Annulaire|Partielle|Hybride)/i)?.[1]
    || '--';
  const gsty = _typeStyle(typeGlobal);
  const elTypeGlobal = document.getElementById('eclipse-type2');
  if (elTypeGlobal) {
    elTypeGlobal.textContent = `${gsty.icon} ${_displayEclipseType(typeGlobal)}`;
    elTypeGlobal.style.color = gsty.color;
  }

  // TYPE À LA POSITION GPS — _type calculé par Jubier pour les coords saisies
  const typeGps = data._type || '--';
  const lsty = _typeStyle(typeGps);
  const elTypeGps = document.getElementById('eclipse-type-gps');
  if (elTypeGps) {
    elTypeGps.textContent = `${lsty.icon} ${_displayEclipseType(typeGps)}`;
    elTypeGps.style.color = lsty.color;
  }

  const obscurationEl = document.getElementById('eclipse-obscuration');
  if (obscurationEl) {
    const obscuration = Number(data._obscuration_percent);
    obscurationEl.textContent = Number.isFinite(obscuration)
      ? `${obscuration.toFixed(2)} %`
      : '--';
  }

  // Compatibilité — champ eclipse-type (non visible mais utilisé ailleurs)
  document.getElementById('eclipse-type') && (document.getElementById('eclipse-type').textContent = typeGps);

  // Trigger — display global eclipse type and GPS-position type together.
  const lbl2 = document.getElementById('trig-eclipse-type2');
  if (lbl2) {
    lbl2.textContent = `${gsty.icon} ${_displayEclipseType(typeGlobal)}`;
    lbl2.style.color = gsty.color;
  }

  const lbl3 = document.getElementById('trig-eclipse-type-gps');
  if (lbl3) {
    lbl3.textContent = `${lsty.icon} ${_displayEclipseType(typeGps)}`;
    lbl3.style.color = lsty.color;
  }

  // TMAX toujours recalculé — le TMAX Jubier (magnitude max) ≠ milieu totalité
  // Totale → C2 + (C3-C2)/2  |  Partielle → C1 + (C4-C1)/2
  const tmilieu = _calcTmax(data.C1 || data.c1, data.C2 || data.c2,
                             data.C3 || data.c3, data.C4 || data.c4)
               || data.TMAX || data.tmax || null;

  const contacts = [
    { key: 'TSTART',  utc: data.TSTART || data.tstart, local_json: null, label: 'TSTART', desc: 'Sequence start', style: 'color:var(--green)' },
    { key: 'C1',      utc: data.C1 || data.c1,          local_json: data.C1_local,  label: 'C1',  desc: 'First contact', style: '' },
    { key: 'C2',      utc: data.C2 || data.c2,          local_json: data.C2_local,  label: 'C2',  desc: 'Totality start', style: '' },
    { key: 'TMILIEU', utc: tmilieu,                      local_json: null,           label: 'MID', desc: 'Mid-totality', style: '' },
    { key: 'C3',      utc: data.C3 || data.c3,          local_json: data.C3_local,  label: 'C3',  desc: 'Totality end', style: '' },
    { key: 'C4',      utc: data.C4 || data.c4,          local_json: data.C4_local,  label: 'C4',  desc: 'Fourth contact', style: '' },
    { key: 'TEND',    utc: data.TEND  || data.tend,     local_json: null, label: 'TEND',  desc: 'Sequence end', style: 'color:var(--green)' },
  ].map(c => ({
    ...c,
    local: c.local_json || (c.utc ? _utcToLocal(c.utc) : null)
  }));

  // Contacts onglet éclipse
  const list = document.getElementById('contacts-list');
  if (list) {
    const _tzOffset = _getTimezoneOffset();
    const tzLabel = data._timezone
      || (state.gps && state.gps.timezone) || _gpsTimezone
      || (_tzOffset !== null ? `UTC${_tzOffset >= 0 ? '+' : ''}${_tzOffset}` : 'UTC');
    const circumstancesType = (data._type || data._type_global || '').toLowerCase();
    const isPartialContacts = circumstancesType.includes('partielle') || circumstancesType.includes('partial');
    const circumstancesContacts = isPartialContacts
      ? [
          contacts.find(c => c.key === 'C1'),
          {
            key: 'TMAX',
            utc: data.TMAX,
            local: data.TMAX ? _utcToLocal(data.TMAX) : null,
            label: 'TMAX'
          },
          contacts.find(c => c.key === 'C4')
        ]
      : contacts.filter(c => {
      if (c.key === 'TMILIEU') return false;
      if (c.key === 'TSTART' || c.key === 'TEND') return false;
      return true;
    });
    list.innerHTML = circumstancesContacts.map(c => `
      <div class="contact-row" id="cr-${c.key}" style="flex-direction:column;align-items:stretch;gap:4px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="contact-label" style="width:auto;font-size:13px;color:var(--accent2)">${c.label}</span>
          <span class="contact-countdown" id="cd-${c.key}" style="font-size:12px">--</span>
        </div>
        <div style="display:flex;gap:16px;font-family:var(--mono);font-size:13px">
          <span>
            <span style="color:var(--text-dim);font-size:10px">UTC </span>
            <span style="color:var(--blue)">${c.utc || '--'}</span>
          </span>
          <span style="color:var(--text-dim);font-size:10px;font-family:var(--mono)">Timezone ${tzLabel}</span>
          <span>
            <span style="color:var(--text-dim);font-size:10px">Local </span>
            <span style="color:var(--orange)">${c.local && c.local !== c.utc ? c.local : (c.utc || '--')}</span>
          </span>
        </div>
      </div>`).join('');
  }
  // Mettre à jour les timezones depuis l'état GPS
  if (state.gps && state.gps.timezone) {
    ['C1', 'C2', 'C3', 'C4'].forEach(key => {
      const tzEl = document.getElementById(`tz-${key}`);
      if (tzEl) tzEl.textContent = state.gps.timezone;
    });
  }
  // Contacts onglet Trigger — design compact
  const tlist = document.getElementById('trigger-contacts');

  const circumstancesType = (
    data._type || data._type_global || ''
  ).toLowerCase();

  const c2Value = data.C2 || data.c2 || null;
  const c3Value = data.C3 || data.c3 || null;

  const isPartialTrigger = (
    circumstancesType.includes('partielle')
    || circumstancesType.includes('partial')
    || !c2Value
    || !c3Value
  );

  let triggerContacts;

  if (isPartialTrigger) {
    triggerContacts = [
      contacts.find(c => c.key === 'TSTART'),
      contacts.find(c => c.key === 'C1'),
      {
        key: 'TMAX',
        utc: data.TMAX || tmilieu,
        local: _utcToLocal(data.TMAX || tmilieu),
        label: 'TMAX'
      },
      contacts.find(c => c.key === 'C4'),
      contacts.find(c => c.key === 'TEND'),
    ].filter(Boolean);
  } else {
    const diamondDuration = state.triggerDiamondDurationS;

    const diamondBefore = Number.isFinite(diamondDuration)
      ? _fromSec(_toSec(c2Value) - diamondDuration)
      : null;

    const diamondAfter = Number.isFinite(diamondDuration)
      ? _fromSec(_toSec(c3Value) + diamondDuration)
      : null;

    triggerContacts = [
      contacts.find(c => c.key === 'TSTART'),
      contacts.find(c => c.key === 'C1'),
      {
        key: 'DR_C2',
        utc: diamondBefore,
        local: diamondBefore ? _utcToLocal(diamondBefore) : null,
        label: 'DIAMOND RING'
      },
      contacts.find(c => c.key === 'C2'),
      contacts.find(c => c.key === 'TMILIEU'),
      contacts.find(c => c.key === 'C3'),
      {
        key: 'DR_C3',
        utc: diamondAfter,
        local: diamondAfter ? _utcToLocal(diamondAfter) : null,
        label: 'DIAMOND RING'
      },
      contacts.find(c => c.key === 'C4'),
      contacts.find(c => c.key === 'TEND'),
    ].filter(Boolean);
  }

  const _triggerDisplayHms = value => {
    if (!value || value === '--') return value || '--';

    const match = String(value).match(
      /^(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?$/
    );

    if (!match) return value;

    return [
      String(match[1]).padStart(2, '0'),
      match[2],
      match[3]
    ].join(':');
  };

  const _buildContactsHtml = () => {
    return triggerContacts.map(c => {
      const isTmax  = c.key === 'TMILIEU';
      const isBound = c.key === 'TSTART' || c.key === 'TEND';
      const labelColor = isTmax ? 'var(--accent)' : isBound ? 'var(--green)' : 'var(--text-dim)';
      const rowBorder  = isTmax ? 'border-color:rgba(245,166,35,.3)'
                       : isBound ? 'border-color:rgba(61,220,132,.2)' : '';
      return `<div class="contact-row" id="${c.key}"
          style="display:flex;justify-content:space-between;align-items:center;${rowBorder}">
        <span class="trigger-contact-label" style="font-family:var(--mono);font-size:11px;color:${labelColor}">${c.label}</span>
        <span style="font-family:var(--mono);font-size:13px;color:var(--blue);flex:1;text-align:center">${_triggerDisplayHms(c.utc)}</span>
        <span style="font-family:var(--mono);font-size:11px;color:var(--accent);flex:1;text-align:center">${_triggerDisplayHms(c.local)}</span>
        <span class="contact-countdown" id="td-${c.key}" style="font-family:var(--mono);font-size:11px;min-width:72px;text-align:right">--</span>
      </div>`;
    }).join('');
  };
  if (tlist) tlist.innerHTML = _buildContactsHtml();

  // Mettre à jour les timezones
  if (state.gps && state.gps.timezone) {
    contacts.forEach(c => {
      const el = document.getElementById('tz-' + c.key);
      if (el) el.textContent = state.gps.timezone;
    });
  }

  // Pré-remplir overrides (TSTART/TEND uniquement — C1/C2/C3/C4 gérés dans l'onglet Éclipse)
  populateOverrides(data);
}

function updateCountdowns(data) {
  if (!data) return;
  const toSec  = hms => { try { const [h,m,s] = hms.split(':').map(Number); return h*3600+m*60+(s||0); } catch(e){return null;} };
  const fromSec = s => `${fmt(Math.floor(s/3600))}:${fmt(Math.floor((s%3600)/60))}:${fmt(Math.floor(s%60))}`;

  // Calculer TMAX selon le type d'éclipse
  // Totale → C2 + (C3-C2)/2  |  Partielle → C1 + (C4-C1)/2
  let tmilieu = data.TMAX || data.tmax || null;
  if (!tmilieu) {
    tmilieu = _calcTmax(data.C1 || data.c1, data.C2 || data.c2,
                        data.C3 || data.c3, data.C4 || data.c4);
  }

  const contacts = {
    TSTART:  data.TSTART || data.tstart,
    C1:      data.C1 || data.c1,
    C2:      data.C2 || data.c2,
    TMILIEU: tmilieu,
    TMAX:    data.TMAX || tmilieu,
    C3:      data.C3 || data.c3,
    C4:      data.C4 || data.c4,
    TEND:    data.TEND || data.tend,
  };

  const diamondDuration = state.triggerDiamondDurationS;

  if (
    Number.isFinite(diamondDuration)
    && contacts.C2
    && contacts.C3
  ) {
    contacts.DR_C2 = _fromSec(_toSec(contacts.C2) - diamondDuration);
    contacts.DR_C3 = _fromSec(_toSec(contacts.C3) + diamondDuration);
  }

  // UTC courant provenant exclusivement de l'ancre Pi.
  const nowUtcMs = _nowAdjusted().getTime();
  const eclipseDateUtc = data._date || data._date_utc || (data._generated_utc ? String(data._generated_utc).slice(0,10) : null);

  let nextKey  = null;
  let nextDiff = Infinity;

  Object.entries(contacts).forEach(([k, t]) => {
    if (!t) return;

    // Mode réel : _date + heures UTC. Le dry-run rebase cette timeline côté backend.
    let diff;
    if (eclipseDateUtc) {
      const targetMs = Date.parse(`${eclipseDateUtc}T${t}Z`);
      if (!Number.isFinite(targetMs)) return;
      diff = (targetMs - nowUtcMs) / 1000;
    } else {
      // Compatibilité vieux JSON sans date : fallback HH:MM:SS avec fenêtre ±12 h.
      const nowUtc = new Date(nowUtcMs);
      const nowUtcSec = nowUtc.getUTCHours()*3600+nowUtc.getUTCMinutes()*60+nowUtc.getUTCSeconds();
      const contactUtcSec = toSec(t);
      if (contactUtcSec === null) return;
      diff = contactUtcSec - nowUtcSec;
      if (diff < -43200) diff += 86400;
      if (diff >  43200) diff -= 86400;
    }

    const abs  = Math.abs(diff);
    const sign = diff > 0 ? '\u2212' : '+';
    const str  = `${sign}${fmt(Math.floor(abs/3600))}:${fmt(Math.floor((abs%3600)/60))}:${fmt(Math.floor(abs%60))}`;

    // Mettre à jour countdowns (onglet éclipse cd- et trigger td-)
    ['cd-', 'td-'].forEach(p => {
      const el = document.getElementById(p + k);
      if (el) {
        el.textContent = str;
        el.style.color = diff < 0 ? 'var(--text-dim)' : diff < 120 ? 'var(--accent)' : 'var(--text)';
      }
    });

    // Highlight contact row actif (onglet éclipse cr-, trigger = ID direct)
    ['cr-'].forEach(p => {
      const row = document.getElementById(p + k);
      if (row) row.classList.toggle('active-phase', diff >= 0 && diff < 120);
    });
    const trigRow = document.getElementById(k);
    if (trigRow && trigRow.classList.contains('contact-row'))
      trigRow.classList.toggle('active-phase', diff >= 0 && diff < 120);

    if (diff > 0 && diff < nextDiff) { nextDiff = diff; nextKey = k; }
  });

  // Countdown anneau
  if (nextKey && nextDiff < Infinity) {
    const rc = document.getElementById('ring-countdown');
    const rl = document.getElementById('ring-label');
    if (rc) rc.textContent = fromSec(nextDiff);
    if (rl) rl.textContent = `→ ${nextKey}`;
  }
}

function fmt(n) { return String(n).padStart(2,'0'); }

// ════════════════════════════════════════════════════════════════
// ACTIONS
// ════════════════════════════════════════════════════════════════
// Copie ponctuelle de la dernière position GPS dans le formulaire Eclipse.
// Cette fonction ne doit être appelée qu'après une action opérateur demandant
// explicitement une acquisition de position.
function copyGpsLocationToEclipseForm(gps) {
  if (!gps) return;

  const latEl = document.getElementById('inp-lat');
  const lonEl = document.getElementById('inp-lon');
  const altEl = document.getElementById('inp-alt');
  const tzEl  = document.getElementById('inp-tz');

  if (latEl && gps.lat != null) latEl.value = gps.lat.toFixed(5);
  if (lonEl && gps.lon != null) lonEl.value = gps.lon.toFixed(5);
  if (altEl && gps.alt != null) altEl.value = Math.round(gps.alt);

  if (tzEl && gps.timezone) {
    const m = gps.timezone.match(/UTC([+-]?\d+(?:\.\d+)?)/);
    if (m) tzEl.value = parseFloat(m[1]);
  }
}

let _pendingGpsLocationCopy = false;

const GPS_ACTION_BUTTONS = {
  'btn-gps-sync-time-location': 'SYNC TIME & LOCATION',
  'btn-gps-sync-time': 'SYNC TIME',
  'btn-gps-get-location': 'GET LOCATION',
};

function resetGpsActionButtons() {
  Object.entries(GPS_ACTION_BUTTONS).forEach(([id, label]) => {
    const btn = document.getElementById(id);
    if (btn) { btn.disabled = false; btn.textContent = label; }
  });
}

async function runGpsAction(buttonId, request, startedMessage, copyLocation = false) {
  _pendingGpsLocationCopy = copyLocation;

  const buttons = Object.keys(GPS_ACTION_BUTTONS).map(id => document.getElementById(id)).filter(Boolean);
  const btn = document.getElementById(buttonId);
  buttons.forEach(button => { button.disabled = true; });
  if (btn) btn.textContent = '⏳ WORKING…';
  try {
    const r = await request;
    const d = await r.json();
    if (!r.ok || d.error) {
      _pendingGpsLocationCopy = false;
      flash(d.error || `HTTP error ${r.status}`, 'red');
      resetGpsActionButtons();
    } else {
      flash(startedMessage, 'blue');
      // Fallback : réactiver après 90s si gps_sync_done n'arrive pas
      setTimeout(resetGpsActionButtons, 90000);
    }
  } catch(e) {
    _pendingGpsLocationCopy = false;
    flash('Network error', 'red');
    resetGpsActionButtons();
  }
}

function syncGpsTimeLocation() {
  return runGpsAction(
    'btn-gps-sync-time-location',
    fetch('/api/gps/sync_time_location', { method: 'POST' }),
    'Time and location synchronization started…',
    true
  );
}

function syncGpsTime() {
  return runGpsAction(
    'btn-gps-sync-time',
    fetch('/api/gps/sync_time', { method: 'POST' }),
    'Time synchronization started…',
    false
  );
}

function getGpsLocation() {
  return runGpsAction(
    'btn-gps-get-location',
    fetch('/api/gps/get_location', { method: 'POST' }),
    'Location acquisition started…',
    true
  );
}

async function calculateEclipse() {
  const lat = parseFloat(document.getElementById('inp-lat').value);
  const lon = parseFloat(document.getElementById('inp-lon').value);
  const alt = parseFloat(document.getElementById('inp-alt').value) || 0;
  const tz  = parseFloat(document.getElementById('inp-tz').value)  || 0;
  const ecl = document.getElementById('inp-eclipse').value;

  if (isNaN(lat) || isNaN(lon)) { flash('Lat/Lon required', 'red'); return; }

  const btn = document.getElementById('btn-calc');
  btn.disabled = true; btn.textContent = '⏳ Calculation in progress…';

  try {
    const r = await fetch('/api/eclipse/calculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lon, alt, tz, eclipse: ecl })
    });
    const d = await r.json();
    if (d.error) flash(d.error, 'red');
  } catch(e) { flash('Network error', 'red'); }
  // Le bouton est réactivé par l'événement eclipse_calculated
}

function _formatUtcOffset(minutes) {
  if (!Number.isFinite(Number(minutes))) return null;
  const value = Number(minutes);
  const sign = value >= 0 ? '+' : '-';
  const absolute = Math.abs(value);
  const hours = Math.floor(absolute / 60);
  const mins = absolute % 60;
  return `UTC${sign}${hours}${mins ? ':' + String(mins).padStart(2, '0') : ''}`;
}

function updateCameraTimeSync(camera, gps, responseAttemptedAt) {
  const sync = camera.time_sync || camera || {};
  const timezoneName = sync.timezone_name || gps.timezone_name;
  const offset = _formatUtcOffset(sync.utc_offset_minutes ?? gps.utc_offset_minutes)
    || gps.timezone || 'UTC';
  const timezone = document.getElementById('cam-time-sync-timezone');
  const status = document.getElementById('cam-time-sync-status');
  const attempted = document.getElementById('cam-time-sync-attempted');
  const message = document.getElementById('cam-time-sync-message');
  if (timezone) timezone.textContent = timezoneName ? `${timezoneName} — ${offset}` : offset;
  if (status) {
    status.textContent = sync.status || '--';
    status.style.color = sync.status === 'success' ? 'var(--green)'
      : sync.status ? 'var(--yellow)' : 'var(--text)';
  }
  if (attempted) attempted.textContent = sync.attempted_at || responseAttemptedAt || '--';
  if (message) message.textContent = sync.message || 'No synchronization requested.';
}

async function syncCameraTime() {
  const btn = document.getElementById('btn-cam-time-sync');
  const requestedAt = new Date().toISOString();
  btn.disabled = true;
  btn.textContent = '⏳ Synchronization in progress…';
  try {
    const r = await fetch('/api/camera/sync_time', { method: 'POST' });
    const d = await r.json();
    if (!r.ok || d.error) {
      updateCameraTimeSync({status: 'error', message: d.error || `HTTP error ${r.status}`}, state.gps || {}, requestedAt);
      flash(d.error || 'Camera synchronization failed', 'red');
    } else {
      updateCameraTimeSync(d, state.gps || {}, requestedAt);
      flash(d.message || 'Camera synchronization completed', d.status === 'success' ? 'green' : 'yellow');
      await loadCameraStatus();
    }
  } catch(e) {
    updateCameraTimeSync({status: 'error', message: 'Network error'}, state.gps || {}, requestedAt);
    flash('Network error', 'red');
  } finally {
    btn.disabled = false;
    btn.textContent = "🕒 Synchronize camera time";
  }
}

async function loadTriggerConfigList() {
  try {
    const requests = await Promise.all([
      fetch('/api/configs/list_eclipse'),
      fetch('/api/configs/list_photo'),
      fetch('/api/configs/list_exposure_opt')
    ]);
    const payloads = await Promise.all(requests.map(response => response.json()));
    const specs = [
      ['trigger-circumstances-select', payloads[0].files || [], '— Select circumstances —'],
      ['trigger-photo-select', payloads[1].files || [], '— Select Photo Setup —'],
      ['trigger-exposure-opt-select', payloads[2].files || [], '— Select Exposure Optimization —']
    ];
    specs.forEach(([id, files, label]) => {
      const select = document.getElementById(id);
      if (!select) return;
      const previous = select.value;
      select.innerHTML = `<option value="">${label}</option>`;
      files.forEach(item => {
        const filename = typeof item === 'string' ? item : item.name;
        const option = document.createElement('option');
        option.value = filename;
        option.textContent = filename;
        select.appendChild(option);
      });
      if (Array.from(select.options).some(option => option.value === previous)) {
        select.value = previous;
      }
    });

    await restoreActiveTriggerInputs();
  } catch (e) {
    flash(`Trigger input list: ${e.message}`, 'red');
  }
}

let _activeTriggerInputsRestoreKey = '';


function _clearRuntimeActiveTriggerOptions() {
  [
    'trigger-circumstances-select',
    'trigger-photo-select',
    'trigger-exposure-opt-select'
  ].forEach(selectId => {
    const select = document.getElementById(selectId);
    if (!select) return;

    select
      .querySelectorAll('option[data-runtime-active="true"]')
      .forEach(option => option.remove());
  });
}


function _selectRuntimeActiveTriggerInput(selectId, filename) {
  const select = document.getElementById(selectId);
  if (!select || !filename) return false;

  let option = Array.from(select.options).find(
    candidate => candidate.value === filename
  );

  if (!option) {
    // Generated DEBUG circumstances are deliberately absent from list_eclipse.
    // During a live run the autonomous runtime owns the exact input filename,
    // so expose that file as a transient selected option after reconnect.
    option = document.createElement('option');
    option.value = filename;
    option.textContent = filename;
    option.dataset.runtimeActive = 'true';
    select.appendChild(option);
  }

  const changed = select.value !== filename;
  select.value = filename;
  return changed;
}


async function restoreActiveTriggerInputs() {
  const rigState = state.triggerRigs[String(selectedTriggerRigId)] || {};
  const inputs = rigState.inputs;

  if (!inputs || typeof inputs !== 'object') return false;

  const signature = [
    selectedTriggerRigId,
    inputs.circumstances_file || '',
    inputs.photo_file || '',
    inputs.exposure_opt_file || ''
  ].join('|');

  if (
    !inputs.circumstances_file
    && !inputs.photo_file
    && !inputs.exposure_opt_file
  ) {
    _clearRuntimeActiveTriggerOptions();
    _activeTriggerInputsRestoreKey = '';
    return false;
  }

  const mappings = [
    ['circumstances_file', 'trigger-circumstances-select'],
    ['photo_file', 'trigger-photo-select'],
    ['exposure_opt_file', 'trigger-exposure-opt-select']
  ];

  let restored = false;

  for (const [field, selectId] of mappings) {
    const filename = inputs[field];
    if (!filename) continue;

    restored = (
      _selectRuntimeActiveTriggerInput(selectId, filename)
      || restored
    );
  }

  const diamondMissing = (
    Boolean(inputs.photo_file)
    && !Number.isFinite(state.triggerDiamondDurationS)
  );

  if (
    _activeTriggerInputsRestoreKey === signature
    && !restored
    && !diamondMissing
  ) {
    return false;
  }

  if (inputs.photo_file) {
    await loadTriggerDiamondDuration();
  }

  const circumstances = state.triggerCircumstances || state.eclipse;
  if (circumstances) {
    state.triggerCircumstances = circumstances;
    renderContacts(circumstances);
  }

  _activeTriggerInputsRestoreKey = signature;
  syncDebugUiFromTrigger();
  return true;
}

async function loadTriggerCircumstances(filename) {
  if (!filename) return;
  try {
    const r = await fetch(`/api/configs/load_circumstances/${encodeURIComponent(filename)}`);
    const d = await r.json();
    if (!r.ok || d.error) {
      throw new Error(d.error || `HTTP error ${r.status}`);
    }

    state.triggerCircumstances = d;
    await loadTriggerDiamondDuration();
    renderContacts(d);
  } catch (e) {
    flash(`Circumstances: ${e.message}`, 'red');
  }
}

async function loadTriggerDiamondDuration() {
  state.triggerDiamondDurationS = null;

  const filename = document.getElementById('trigger-photo-select')?.value || '';
  if (!filename) return;

  try {
    const r = await fetch(`/api/configs/load_photo/${encodeURIComponent(filename)}`);
    const data = await r.json();

    if (!r.ok || data.error) return;

    const value = Number(data?.phases?.diamond_ring?.duration_s);
    if (Number.isFinite(value) && value >= 0) {
      state.triggerDiamondDurationS = value;
    }
  } catch (_error) {
    state.triggerDiamondDurationS = null;
  }
}

async function refreshTriggerCircumstancesForPhoto() {
  await loadTriggerDiamondDuration();

  if (state.triggerCircumstances) {
    renderContacts(state.triggerCircumstances);
  }
}

function selectedTriggerInputs() {
  return {
    circumstances_file: document.getElementById('trigger-circumstances-select')?.value || '',
    photo_file: document.getElementById('trigger-photo-select')?.value || '',
    exposure_opt_file: document.getElementById('trigger-exposure-opt-select')?.value || ''
  };
}

async function startTrigger() {
  const rigIds = activeTriggerRigIds();
  const inputs = selectedTriggerInputs();

  if (!rigIds.length) {
    flash('No active RIG.', 'red');
    return;
  }

  const failures = [];

  for (const rigId of rigIds) {
    try {
      const r = await fetch('/api/trigger/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          rig_id: rigId,
          ...inputs
        })
      });

      const d = await r.json();

      if (!r.ok || d.error) {
        failures.push({
          rigId,
          error: d.message || d.error || `HTTP error ${r.status}`,
          code: d.code
        });
      }
    } catch (error) {
      failures.push({
        rigId,
        error: error.message || 'Network error'
      });
    }
  }

  if (failures.length) {
    flash(
      failures
        .map(item => `RIG ${item.rigId}: ${item.error}`)
        .join(' | '),
      'red'
    );

    const codes = new Set(failures.map(item => item.code));

    if (
      codes.has('GPS_NOT_SYNCED') ||
      codes.has('GPS_SYNC_STALE') ||
      codes.has('GPS_SYNC_TIME_INVALID')
    ) {
      setTimeout(() => showTab(1), 1500);
    } else if (codes.has('JSON_INVALID')) {
      setTimeout(() => showTab(2), 1500);
    }

    return;
  }

  flash(
    rigIds.length > 1
      ? `Trigger started on ${rigIds.length} RIGs ▶`
      : `Trigger started on RIG ${rigIds[0]} ▶`,
    'green'
  );
}


async function startDebug() {
  const rigIds = activeTriggerRigIds();
  const inputs = selectedTriggerInputs();

  if (!rigIds.length) {
    flash('No active RIG.', 'red');
    return;
  }

  if (!inputs.photo_file || !inputs.exposure_opt_file) {
    flash(
      'DEBUG requires a selected Photo Setup and Exposure Optimization file.',
      'red'
    );
    return;
  }

  const targetText = rigIds.length > 1
    ? `${rigIds.length} active RIGs`
    : `RIG ${rigIds[0]}`;

  if (!confirm(
    `🧪 DEBUG MODE — ${targetText}\n\n` +
    'This will generate one short DEBUG circumstances file per active RIG,\n' +
    'load it for that RIG and START all sequences immediately.\n' +
    'The currently selected Photo Setup and Exposure Optimization will be used.\n\n' +
    'Continue?'
  )) return;

  const failures = [];
  const results = [];
  // One absolute UTC anchor is shared by every RIG in this DEBUG ALL run.
  // The requests remain sequential for generated-state safety, but their
  // eclipse circumstances are now bit-for-bit time aligned.
  const debugAnchorUtc = new Date().toISOString();

  // Deliberately sequential: /debug updates generated circumstances state.
  // Running these requests concurrently would introduce a race.
  for (const rigId of rigIds) {
    try {
      const r = await fetch('/api/trigger/debug', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          rig_id: rigId,
          debug_anchor_utc: debugAnchorUtc,
          photo_file: inputs.photo_file,
          exposure_opt_file: inputs.exposure_opt_file
        })
      });

      const d = await r.json();

      if (!r.ok || d.error) {
        failures.push({
          rigId,
          error: d.message || d.error || `HTTP error ${r.status}`,
          code: d.code
        });
      } else {
        results.push(d);
      }
    } catch (error) {
      failures.push({
        rigId,
        error: error.message || 'Network error'
      });
    }
  }

  // Display the generated DEBUG circumstances without changing any of the
  // operator's normal Trigger selections.  DEBUG is temporary execution
  // state; Circumstances / Photo Setup / Exposure Optimization remain the
  // prepared inputs for the next normal START.
  const displayed = (
    results.find(item => Number(item.rig_id) === selectedTriggerRigId)
    || results[0]
  );

  if (displayed && displayed.circumstances) {
    state.triggerCircumstances = displayed.circumstances;
    renderContacts(displayed.circumstances);
  }

  if (failures.length) {
    flash(
      failures
        .map(item => `RIG ${item.rigId}: ${item.error}`)
        .join(' | '),
      'red'
    );

    const codes = new Set(failures.map(item => item.code));
    if (
      codes.has('GPS_NOT_SYNCED') ||
      codes.has('GPS_SYNC_STALE') ||
      codes.has('GPS_SYNC_TIME_INVALID')
    ) {
      setTimeout(() => showTab(1), 1500);
    }

    return;
  }

  flash(
    rigIds.length > 1
      ? `DEBUG started on ${rigIds.length} RIGs`
      : `DEBUG started on RIG ${rigIds[0]}`,
    'blue'
  );
}


async function startDryRun() {
  const rigIds = activeTriggerRigIds();

  if (!rigIds.length) {
    flash('No active RIG.', 'red');
    return;
  }

  if (!confirm(
    rigIds.length > 1
      ? `🧪 Start a DRY-RUN on all ${rigIds.length} active RIGs?\n` +
        'The selected circumstances will use their original UTC times,\n' +
        'using today\'s UTC date. Sounds are included.'
      : '🧪 Start a DRY-RUN?\n' +
        'The selected circumstances will use their original UTC times,\n' +
        'using today\'s UTC date. Sounds are included.'
  )) return;

  const inputs = selectedTriggerInputs();
  const failures = [];

  for (const rigId of rigIds) {
    try {
      const r = await fetch('/api/trigger/dryrun', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          rig_id: rigId,
          ...inputs
        })
      });

      const d = await r.json();

      if (!r.ok || d.error) {
        failures.push({
          rigId,
          error: d.message || d.error || `HTTP error ${r.status}`,
          code: d.code
        });
      }
    } catch (error) {
      failures.push({
        rigId,
        error: error.message || 'Network error'
      });
    }
  }

  if (failures.length) {
    flash(
      failures
        .map(item => `RIG ${item.rigId}: ${item.error}`)
        .join(' | '),
      'red'
    );

    const codes = new Set(failures.map(item => item.code));
    if (
      codes.has('GPS_NOT_SYNCED') ||
      codes.has('GPS_SYNC_STALE') ||
      codes.has('GPS_SYNC_TIME_INVALID')
    ) {
      setTimeout(() => showTab(1), 1500);
    }

    return;
  }

  flash(
    rigIds.length > 1
      ? `Dry-run started on ${rigIds.length} RIGs`
      : `Dry-run started on RIG ${rigIds[0]}`,
    'blue'
  );
}

async function stopTrigger() {
  const rigId = selectedTriggerRigId;
  const rigKey = String(rigId);
  const rigState = state.triggerRigs[rigKey] || {};
  const force = rigState.phase === 'stopping';
  const btn = document.getElementById('btn-stop');
  const debugBtn = document.getElementById('btn-debug-stop');
  const pending = window._triggerStopPendingRigs || new Set();
  window._triggerStopPendingRigs = pending;

  if (pending.has(rigId)) {
    flash(force ? 'Force stop already in progress' : 'Trigger stop already in progress', 'yellow');
    return;
  }

  const confirmed = force
    ? confirm(
        '⚠️ FORCE STOP this RIG now?\n\n' +
        'This sends SIGKILL immediately and can interrupt the atomic PHOTO currently in progress.\n' +
        'Use only when you explicitly want to abort the current camera operation.'
      )
    : confirm(
        '■ Request graceful STOP?\n\n' +
        'Any atomic PHOTO already in progress is allowed to finish safely.\n' +
        'If it does not finish, the STOP button will become FORCE STOP.'
      );
  if (!confirmed) return;

  pending.add(rigId);
  const pendingLabel = force ? '⏳ Force stopping…' : '⏳ Stopping…';
  if (btn) {
    btn.disabled = true;
    btn.textContent = pendingLabel;
  }
  if (debugBtn) {
    debugBtn.disabled = true;
    debugBtn.textContent = pendingLabel;
  }

  try {
    const r = await fetch('/api/trigger/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rig_id: rigId, force})
    });
    const d = await r.json();

    if (!r.ok || d.error) {
      flash(d.error || `HTTP error ${r.status}`, 'red');
      return;
    }

    if (d.status === 'not_running') {
      state.triggerRigs[rigKey] = {
        ...rigState,
        running: false,
        phase: 'idle'
      };
      flash('Trigger not active', 'yellow');
    } else if (d.status === 'stopping') {
      state.triggerRigs[rigKey] = {
        ...rigState,
        running: true,
        phase: 'stopping'
      };
      flash(
        force
          ? '⚠️ FORCE STOP sent — process exit pending'
          : '■ Graceful STOP requested — waiting for current atomic PHOTO',
        force ? 'red' : 'yellow'
      );
    } else {
      state.triggerRigs[rigKey] = {
        ...rigState,
        running: Boolean(d.still_running),
        phase: d.still_running ? 'stopping' : 'idle'
      };
      flash(force ? '■ Trigger force-stopped' : '■ Trigger stopped', 'yellow');
    }
  } catch(e) {
    flash('Network error while stopping', 'red');
  } finally {
    pending.delete(rigId);
    updateSelectedTriggerPhase();
    syncDebugActionState();
  }
}

async function startTotalityOnly() {
  if (!confirm(
    '🌑 START EMERGENCY TOTALITY SEQUENCE NOW?\n' +
    'Works even when the normal trigger is not running.\n' +
    'If active, the current PHOTO sequence is replaced immediately.\n' +
    'Press STOP to stop this RIG.'
  )) return;

  try {
    const r = await fetch('/api/trigger/totality_only', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rig_id: selectedTriggerRigId})
    });

    const d = await r.json();

    if (r.ok && d.status === 'ok') {
      flash(
        d.action === 'preempted'
          ? '🌑 Totality override active — timing audio continues'
          : '🌑 Emergency Totality sequence started immediately',
        'orange'
      );
      document.getElementById('btn-totality-only').style.opacity = '0.5';
    } else {
      flash(d.error || 'Totality override failed', 'red');
    }
  } catch(e) {
    flash('Network error', 'red');
  }
}

async function testSound(_file) {
  // One TEST action validates both outputs. The backend owns the Pi playback
  // and broadcasts audio_play back to this and all other connected browsers.
  try {
    const response = await fetch('/api/audio/test', {
      method: 'POST',
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      flash(data.error || 'Audio test failed', 'red');
    } else if (data.status === 'muted') {
      flash('Sound is OFF', 'yellow');
    }
  } catch (_error) {
    flash('Audio test network error', 'red');
  }
}

function applySoundsEnabled(enabled) {
  state.soundsEnabled = enabled === true;

  const toggle = document.getElementById('toggle-sounds');
  if (toggle) {
    toggle.classList.toggle('on', state.soundsEnabled);
  }
}


async function toggleSounds() {
  const requested = !state.soundsEnabled;

  try {
    const response = await fetch('/api/audio/enabled', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: requested}),
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      flash(data.error || 'Unable to change audio state', 'red');
      return;
    }

    applySoundsEnabled(data.enabled);
  } catch (_error) {
    flash('Audio control network error', 'red');
  }
}

let audioVolumeSyncTimer = null;

function applyVolume(v) {
  const percent = Math.max(0, Math.min(100, Number(v) || 0));
  state.volume = percent / 100;

  const slider = document.getElementById('volume-slider');
  if (slider && Number(slider.value) !== Math.round(percent)) {
    slider.value = Math.round(percent);
  }

  const label = document.getElementById('volume-label');
  if (label) label.textContent = `${Math.round(percent)}%`;
}

function setVolume(v) {
  applyVolume(v);

  if (audioVolumeSyncTimer) clearTimeout(audioVolumeSyncTimer);
  audioVolumeSyncTimer = setTimeout(async () => {
    audioVolumeSyncTimer = null;
    try {
      const response = await fetch('/api/audio/volume', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({volume: state.volume}),
      });
      const data = await response.json();
      if (!response.ok || data.error) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      applyVolume(Number(data.volume) * 100);
    } catch (error) {
      flash(`Pi audio volume: ${error.message}`, 'red');
    }
  }, 120);
}

// Initialiser le switch et le slider depuis l'état partagé de la Pi.
document.addEventListener('DOMContentLoaded', () => {
  const sw = document.getElementById('toggle-sounds');
  if (sw) sw.classList.toggle('on', state.soundsEnabled);
  applyVolume(state.volume * 100);

  fetch('/api/audio/enabled')
    .then(response => response.json())
    .then(data => {
      if (typeof data.enabled === 'boolean') {
        applySoundsEnabled(data.enabled);
      }
    })
    .catch(() => {});

  fetch('/api/audio/volume')
    .then(response => response.json())
    .then(data => {
      if (Number.isFinite(Number(data.volume))) {
        applyVolume(Number(data.volume) * 100);
      }
    })
    .catch(() => {});
});

// ════════════════════════════════════════════════════════════════
// BATTERIE
// ════════════════════════════════════════════════════════════════
function updateBattery(pct) {
  if (pct == null) return;
  const el  = document.getElementById('battery-pct');
  const bar = document.getElementById('battery-bar');
  const msg = document.getElementById('battery-msg');
  if (!el) return;

  el.textContent = `${pct}%`;
  bar.style.width = `${Math.min(pct, 100)}%`;

  let color, msgText = '';
  if      (pct > 50) { color = 'var(--green)';   msgText = ''; }
  else if (pct > 20) { color = 'var(--yellow)';  msgText = 'Battery OK'; }
  else               { color = 'var(--red)';     msgText = '⚠⚠ CRITICAL — replace as soon as possible'; }

  el.style.color    = color;
  bar.style.background = color;
  if (msg) msg.textContent = msgText;
  if (msg) msg.style.color = color;

  // Dot caméra header
  const dot = document.getElementById('dot-camera');
  if (dot) dot.className = pct > 20 ? 'dot on' : 'dot off';
}

// ════════════════════════════════════════════════════════════════
// OVERRIDES — champs modifiables
// ════════════════════════════════════════════════════════════════
function populateOverrides(data) {
  if (!data) return;
  const set = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
  set('ov-tstart', data.TSTART || data.tstart);
  set('ov-tend',   data.TEND   || data.tend);
  // C1/C2/C3/C4 gérés dans l'onglet Éclipse — pas de champ ov-c* dans Trigger
  // Paramètres phases
  if (data.phase1a) {
    set('ov-p1a-interval', data.phase1a.interval_s);
    set('ov-p1a-speed',    data.phase1a.speed_denom);
  }
  if (data.diamond_ring) {
    set('ov-dr-duration', data.diamond_ring.duration_s);
    set('ov-dr-interval', data.diamond_ring.interval_s);
    set('ov-dr-speed',    data.diamond_ring.speed_denom);
  }
  // Grille vitesses totalité
  if (data.totality && data.totality.speeds) {
    initSpeedsGrid(data.totality.speeds);
  } else {
    initSpeedsGrid();
  }
  if (data.phase3b) {
    // Phase 3b utilise les mêmes champs que 1a
    set('ov-p1a-interval', data.phase3b.interval_s);
    set('ov-p1a-speed',    data.phase3b.speed_denom);
  }
}

async function saveOverrides() {
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };

  // Construire le payload à partir des champs remplis
  const payload = {};
  const tstart = get('ov-tstart'); if (tstart) payload.TSTART = tstart;
  const tend   = get('ov-tend');   if (tend)   payload.TEND   = tend;
  // C1/C2/C3/C4 gérés dans l'onglet Éclipse

  // Paramètres phases
  const p1aInt = get('ov-p1a-interval'); const p1aSpd = get('ov-p1a-speed');
  if (p1aInt || p1aSpd) {
    payload.phase1a = {};
    if (p1aInt) payload.phase1a.interval_s   = parseInt(p1aInt);
    if (p1aSpd) payload.phase1a.speed_denom  = parseInt(p1aSpd);
    // Phase 3b = mêmes valeurs que 1a (champs unifiés)
    payload.phase3b = { ...payload.phase1a };
  }
  const drDur = get('ov-dr-duration'); const drInt = get('ov-dr-interval'); const drSpd = get('ov-dr-speed');
  if (drDur || drInt || drSpd) {
    payload.diamond_ring = {};
    if (drDur) payload.diamond_ring.duration_s  = parseInt(drDur);
    if (drInt) payload.diamond_ring.interval_s  = parseInt(drInt);
    if (drSpd) payload.diamond_ring.speed_denom = parseInt(drSpd);
  }
  // Vitesses de totalité (cochées)
  const speedCheckboxes = document.querySelectorAll('#totality-speeds-grid input[type="checkbox"]:checked');
  const selectedSpeeds = Array.from(speedCheckboxes).map(cb =>
    DEFAULT_SPEEDS[parseInt(cb.id.split('-')[1])]
  );
  if (selectedSpeeds.length > 0) {
    payload.totality = { speeds: selectedSpeeds };
  }

  try {
    const r = await fetch('/api/eclipse/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (d.status === 'ok') flash('Settings saved ✓', 'green');
    else flash(d.error || 'Error', 'red');
  } catch(e) { flash('Network error', 'red'); }
}
// ════════════════════════════════════════════════════════════════
// CONFIG FILES
// ════════════════════════════════════════════════════════════════
// Vitesses disponibles pour partielle et diamond ring
const PARTIAL_SPEEDS = ['1/8000','1/4000','1/2000','1/1000','1/500','1/400'];

function initPhaseSpeedsGrid(gridId, selectedSpeeds) {
  const grid = document.getElementById(gridId);
  if (!grid) return;
  grid.innerHTML = '';
  PARTIAL_SPEEDS.forEach((v, i) => {
    const cell = document.createElement('div');
    cell.className = 'speed-cell';
    cell.onclick = () => { const cb = cell.querySelector('input'); cb.checked = !cb.checked; };
    const chk = (selectedSpeeds || []).includes(v) ? 'checked' : '';
    cell.innerHTML = `<input type="checkbox" id="${gridId}-${i}" ${chk} onclick="event.stopPropagation()"><label for="${gridId}-${i}">${v}</label>`;
    grid.appendChild(cell);
  });
}

function getPhaseSpeedSelection(gridId) {
  return PARTIAL_SPEEDS.filter((v, i) => {
    const cb = document.getElementById(`${gridId}-${i}`);
    return cb && cb.checked;
  });
}

async function loadConfigFileList() {
  try {
    const r = await fetch('/api/configs/list');
    const d = await r.json();
    const sel = document.getElementById('config-file-select');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">— Select a file —</option>';
    (d.files || []).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      if (f === current) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

async function loadEclipseFileList() {
  try {
    const r = await fetch('/api/configs/list_eclipse');
    const d = await r.json();
    const sel = document.getElementById('eclipse-file-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Circumstances file —</option>';
    (d.files || []).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

async function loadCameraConfigList() {
  try {
    const r = await fetch('/api/configs/list_photo');
    const d = await r.json();
    const sel = document.getElementById('camera-config-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Photo Setup file —</option>';
    (d.files || []).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

async function loadEclipseFile(filename) {
  if (!filename) return;
  try {
    const r = await fetch('/api/configs/load/' + encodeURIComponent(filename));
    if (!r.ok) { flash('Failed to load circumstances', 'red'); return; }
    const data = await r.json();
    const rPost = await fetch('/api/eclipse/override', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await rPost.json();
    if (d.status === 'ok') flash('Circumstances loaded: ' + filename, 'green');
    else flash(d.error || 'Error', 'red');
  } catch(e) { flash('Network error', 'red'); }
}

async function loadCameraConfig(filename) {
  if (!filename) return;
  try {
    const r = await fetch('/api/configs/load_photo/' + encodeURIComponent(filename));
    if (!r.ok) {
      flash('Failed to load Photo Setup configuration', 'red');
      return;
    }
    const data = await r.json();
    _applyCameraConfig(data);
    flash('Photo Setup loaded: ' + filename, 'green');
  } catch(e) {
    flash('Network error', 'red');
  }
}

function _applyCameraConfig(data) {
  const phases = data.phases || {};
  const phaseData = {
    partial: phases.partial || data.partial || {},
    diamond_ring: phases.diamond_ring || data.diamond_ring || {},
    totality: phases.totality || data.totality || {}
  };

  const p = phaseData.partial;
  const dr = phaseData.diamond_ring;
  const sequenceMargin = document.getElementById('cfg-sequence-margin');
  if (sequenceMargin) {
    sequenceMargin.value = data.sequence_margin_min ?? 60;
  }
  if (p.interval_s != null || p.interval != null) {
    document.getElementById('cfg-partial-interval').value = p.interval_s ?? p.interval;
  }
  if (dr.duration_s != null || dr.duration != null) {
    document.getElementById('cfg-dr-duration').value = dr.duration_s ?? dr.duration;
  }
  if (dr.interval_s != null || dr.interval != null) {
    document.getElementById('cfg-dr-interval').value = dr.interval_s ?? dr.interval;
  }
  const overlap = document.getElementById('cfg-dr-overlap');
  if (overlap) {
    overlap.value = dr.totality_overlap_s ?? 5;
  }

  const legacySingleSpeeds = {
    partial: data.shutterspeed_partial,
    diamond_ring: data.shutterspeed_diamondring
  };
  Object.entries(phaseData).forEach(([phase, values]) => {
    const prefix = phase === 'partial' ? 'partial' : phase === 'diamond_ring' ? 'dr' : 'tot';
    document.getElementById(`cfg-${prefix}-iso`).value = values.iso ?? '100';
    document.getElementById(`cfg-${prefix}-aperture`).value = values.aperture ?? 'f/8';

    if (values.shutter_min != null && values.shutter_max != null) {
      _setShutterBounds(prefix, values.shutter_min, values.shutter_max);
      return;
    }
    if (legacySingleSpeeds[phase] != null) {
      _setShutterBounds(prefix, legacySingleSpeeds[phase], legacySingleSpeeds[phase]);
      return;
    }
    if (Array.isArray(values.speeds) && values.speeds.length) {
      _setLegacyShutterBounds(prefix, values.speeds);
    }
  });
}

function _setShutterBounds(prefix, slowest, fastest) {
  document.getElementById(`cfg-${prefix}-shutter-min`).value = String(slowest);
  document.getElementById(`cfg-${prefix}-shutter-max`).value = String(fastest);
}

function _setLegacyShutterBounds(prefix, speeds) {
  const select = document.getElementById(`cfg-${prefix}-shutter-min`);
  const canonicalOrder = Array.from(select.options, option => option.value);
  const ordered = speeds
    .map(String)
    .filter(speed => canonicalOrder.includes(speed))
    .sort((a, b) => canonicalOrder.indexOf(a) - canonicalOrder.indexOf(b));
  if (ordered.length) {
    _setShutterBounds(prefix, ordered[0], ordered[ordered.length - 1]);
  }
}

function _readCameraConfig() {
  const phase = (prefix, interval_s, duration_s) => ({
    iso: parseInt(document.getElementById(`cfg-${prefix}-iso`).value, 10),
    aperture: document.getElementById(`cfg-${prefix}-aperture`).value,
    shutter_min: document.getElementById(`cfg-${prefix}-shutter-min`).value,
    shutter_max: document.getElementById(`cfg-${prefix}-shutter-max`).value,
    step_ev: 1.0,
    enabled: true,
    interval_s,
    duration_s
  });
  const config = {
    schema_version: 2,
    kind: 'capture_execution',
    sequence_margin_min: Number(document.getElementById('cfg-sequence-margin').value),
    phases: {
      partial: phase('partial', parseInt(document.getElementById('cfg-partial-interval').value, 10), null),
      diamond_ring: phase(
        'dr',
        parseInt(document.getElementById('cfg-dr-interval').value, 10),
        parseInt(document.getElementById('cfg-dr-duration').value, 10)
      ),
      totality: phase('tot', 0, null)
    },
    config_type: 'photo_setup'
  };
  config.phases.diamond_ring.totality_overlap_s = Number(
    document.getElementById('cfg-dr-overlap').value
  );
  return config;
}

function buildPreviewIntents() {
  const cameraConfig = _readCameraConfig();
  const phases = cameraConfig && cameraConfig.phases;
  const eclipse = state.eclipse;
  const eclipseDate = eclipse && (eclipse._date || eclipse._date_utc);
  if (!phases || !eclipse || !eclipseDate) return null;

  const phaseTargets = [
    { phase: 'partial', contact: eclipse.C1 || eclipse.TMAX, request_id: 'preview-partial' }
  ];
  if (eclipse.C2 || eclipse.C3) {
    phaseTargets.push(
      { phase: 'diamond_ring', contact: eclipse.C2 || eclipse.C3, request_id: 'preview-diamond-ring' }
    );
  }
  if (eclipse.TMAX) {
    phaseTargets.push(
      { phase: 'totality', contact: eclipse.TMAX, request_id: 'preview-totality' }
    );
  }

  const intents = [];
  for (const target of phaseTargets) {
    const phaseConfig = phases[target.phase];
    if (!phaseConfig || !target.contact
        || phaseConfig.shutter_min == null || phaseConfig.shutter_min === ''
        || phaseConfig.shutter_max == null || phaseConfig.shutter_max === ''
        || phaseConfig.iso == null || Number.isNaN(phaseConfig.iso)) {
      return null;
    }
    intents.push({
      phase: target.phase,
      origin: target.phase,
      request_id: target.request_id,
      target_time: `${eclipseDate}T${target.contact}Z`,
      deadline: null,
      shutter_min: phaseConfig.shutter_min,
      shutter_max: phaseConfig.shutter_max,
      step_ev: phaseConfig.step_ev ?? 1.0,
      iso_target: phaseConfig.iso
    });
  }
  return intents;
}

let rigPreviewInFlight = false;

function renderRigPreviews(responseJson, requestedRigId) {
  const requestedBody = document.getElementById(`rig-preview-${requestedRigId}`);
  if (!requestedBody) return;
  requestedBody.replaceChildren();

  const payload = JSON.parse(responseJson);
  const rigs = Array.isArray(payload && payload.rigs) ? payload.rigs : [];

  const phaseLabels = {
    partial: 'Partial',
    diamond_ring: 'Diamond Ring',
    totality: 'Totality'
  };

  rigs.forEach(rig => {
    if (Number(rig && rig.rig_id) !== Number(requestedRigId)) return;

    const body = document.getElementById(`rig-preview-${rig.rig_id}`);
    if (!body) return;

    const items = Array.isArray(rig.items) ? rig.items : [];

    items.forEach(item => {
      const preview = document.createElement('div');
      preview.className = 'rig-preview-intent';

      const title = document.createElement('div');
      title.className = 'rig-preview-phase';
      title.textContent = phaseLabels[item && item.phase] || item.phase || 'Phase';
      preview.appendChild(title);

      if (item && item.error) {
        const message = document.createElement('div');
        message.className = 'rig-preview-error';
        message.textContent = `Error: ${item.error.message || item.error.code || 'preview unavailable'}`;
        preview.appendChild(message);
      } else {
        const lines = Array.isArray(item && item.diff_lines)
          ? item.diff_lines
          : [];

        if (lines.length === 0) {
          const line = document.createElement('div');
          line.textContent = 'No impact';
          preview.appendChild(line);
        } else {
          lines.forEach(value => {
            const line = document.createElement('div');
            line.textContent = String(value);
            preview.appendChild(line);
          });
        }
      }

      body.appendChild(preview);
    });
  });
}

function _exposureOptLogContainer() {
  return document.getElementById('log-container-exposure_opt');
}

function _exposureOptAddLine(text = '', options = {}) {
  const container = _exposureOptLogContainer();
  if (!container) return;

  const line = document.createElement('div');
  line.textContent = text;

  line.style.fontFamily = 'var(--mono)';
  line.style.fontSize = '11px';
  line.style.lineHeight = '1.55';
  line.style.whiteSpace = 'pre-wrap';
  line.style.wordBreak = 'break-word';

  if (!text) {
    line.style.height = '8px';
  }

  if (options.indent) {
    line.style.paddingLeft = `${options.indent * 14}px`;
  }

  if (options.rig) {
    line.style.color = 'var(--accent)';
    line.style.fontSize = '14px';
    line.style.fontWeight = '700';
    line.style.marginTop = '6px';
    line.style.marginBottom = '4px';
  }

  if (options.section) {
    line.style.color = 'var(--green)';
    line.style.fontSize = '12px';
    line.style.fontWeight = '700';
    line.style.marginTop = '10px';
    line.style.paddingBottom = '4px';
    line.style.borderBottom = '1px solid rgba(61,220,132,.35)';
  }

  if (options.subsection) {
    line.style.fontWeight = '700';
    line.style.marginTop = '4px';
  }

  if (options.dim) {
    line.style.color = 'var(--text-dim)';
  }

  if (options.success) {
    line.style.color = 'var(--green)';
  }

  if (options.warning) {
    line.style.color = 'var(--yellow)';
  }

  if (options.error) {
    line.style.color = 'var(--red)';
  }

  container.appendChild(line);
}

function _exposureOptSeparator() {
  const container = _exposureOptLogContainer();
  if (!container) return;

  const line = document.createElement('div');
  line.style.borderTop = '1px solid var(--border)';
  line.style.margin = '12px 0';
  container.appendChild(line);
}

function _exposureOptFormat(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : 'N/A';
}

function _exposureOptFormatCeiling(value) {
  const seconds = Number(value);

  if (!Number.isFinite(seconds) || seconds <= 0) {
    return 'N/A';
  }

  if (seconds >= 1) {
    return `${seconds.toFixed(2).replace(/\.?0+$/, '')} s`;
  }

  return `1/${Math.round(1 / seconds)} s`;
}

function _exposureOptRigIsActive(rigId) {
  const rig = Array.isArray(rigDevicesState.rigs)
    ? rigDevicesState.rigs.find(
        item => Number(item.rig_id) === Number(rigId)
      )
    : null;

  return Number(rigId) === 1 || Boolean(rig && rig.enabled === true);
}

function renderExposureOptHypotheses(intents) {
  const eclipse = state.eclipse || {};
  const photo = _readCameraConfig();
  const photoSelect = document.getElementById('camera-config-select');

  _exposureOptAddLine('HYPOTHESES', {section:true});

  // ================================================================
  // Eclipse
  // ================================================================

  _exposureOptAddLine('Eclipse', {subsection:true});

  const eclipseDate =
    eclipse._date ||
    eclipse._date_utc ||
    'N/A';

  _exposureOptAddLine(
    `Date : ${eclipseDate}`,
    {indent:1}
  );

  const location =
    eclipse._circumstances_location ||
    eclipse.reference_site ||
    null;

  if (location) {
    const lat = location.latitude ?? location.lat ?? null;
    const lon = location.longitude ?? location.lon ?? null;
    const alt = location.altitude_m ?? location.alt_m ?? null;

    if (lat != null && lon != null) {
      _exposureOptAddLine(
        `Location : ${_exposureOptFormat(lat, 5)}°, ` +
        `${_exposureOptFormat(lon, 5)}°`,
        {indent:1}
      );
    }

    if (alt != null) {
      _exposureOptAddLine(
        `Observer altitude : ${_exposureOptFormat(alt, 0)} m`,
        {indent:1}
      );
    }
  }

  ['C1', 'C2', 'TMAX', 'C3', 'C4'].forEach(contact => {
    if (eclipse[contact]) {
      _exposureOptAddLine(
        `${contact} : ${eclipse[contact]}`,
        {indent:1}
      );
    }
  });

  // ================================================================
  // Photo Setup
  // ================================================================

  _exposureOptAddLine('');
  _exposureOptAddLine('Photo Setup', {subsection:true});

  const photoFilename =
    photoSelect && photoSelect.value
      ? photoSelect.value
      : 'current unsaved UI values';

  _exposureOptAddLine(
    `File : ${photoFilename}`,
    {indent:1}
  );

  const phaseLabels = {
    partial: 'Partial',
    diamond_ring: 'Diamond Ring',
    totality: 'Totality'
  };

  const phases =
    photo && photo.phases
      ? photo.phases
      : {};

  Object.entries(phaseLabels).forEach(([phaseId, label]) => {
    const cfg = phases[phaseId];
    if (!cfg) return;

    _exposureOptAddLine(
      label,
      {subsection:true, indent:1}
    );

    _exposureOptAddLine(
      `ISO : ${cfg.iso ?? 'N/A'}`,
      {indent:2}
    );

    _exposureOptAddLine(
      `Shutter : ${cfg.shutter_max ?? 'N/A'} → ` +
      `${cfg.shutter_min ?? 'N/A'}`,
      {indent:2}
    );

    _exposureOptAddLine(
      `EV step : ${cfg.step_ev ?? 1}`,
      {indent:2}
    );

    if (cfg.interval_s != null) {
      _exposureOptAddLine(
        `Interval : ${cfg.interval_s} s`,
        {indent:2}
      );
    }

    if (cfg.duration_s != null) {
      _exposureOptAddLine(
        `Duration : ${cfg.duration_s} s`,
        {indent:2}
      );
    }
  });

  if (!Array.isArray(intents) || intents.length === 0) {
    _exposureOptAddLine(
      'Preview intents could not be built.',
      {error:true}
    );
  }

  _exposureOptSeparator();
}

function renderExposureOptPreviewLog(
  responseText,
  rigId,
  options = {}
) {
  const container = _exposureOptLogContainer();
  if (!container) return;

  if (options.clear !== false) {
    container.innerHTML = '';
  }

  let payload;

  try {
    payload = JSON.parse(responseText);
  } catch (_) {
    _exposureOptAddLine(
      `RIG ${rigId} — invalid Preview response`,
      {error:true}
    );
    return;
  }

  const rig = Array.isArray(payload.rigs)
    ? payload.rigs.find(
        item => Number(item.rig_id) === Number(rigId)
      )
    : null;

  if (!rig || !Array.isArray(rig.items)) {
    _exposureOptAddLine(`RIG ${rigId}`, {rig:true});
    _exposureOptAddLine(
      'No Preview result returned.',
      {indent:1, error:true}
    );
    return;
  }

  const labels = {
    partial: 'Partial',
    diamond_ring: 'Diamond Ring',
    totality: 'Totality'
  };

  const metadata = rig.metadata || {};
  const atmos = rig.atmospheric || {};

  // ================================================================
  // RIG
  // ================================================================

  _exposureOptAddLine(`RIG ${rigId}`, {rig:true});

  _exposureOptAddLine(
    `Camera : ${metadata.camera || 'N/A'}`,
    {indent:1}
  );

  _exposureOptAddLine(
    `Pixel size : ${
      metadata.pixel_pitch_um == null
        ? 'N/A'
        : `${_exposureOptFormat(metadata.pixel_pitch_um, 2)} µm`
    }`,
    {indent:1}
  );

  _exposureOptAddLine(
    `Mount : ${metadata.mount || 'N/A'}`,
    {indent:1}
  );

  if (metadata.mount_geometry) {
    _exposureOptAddLine(
      `Geometry : ${metadata.mount_geometry}`,
      {indent:1}
    );
  }

  if (metadata.mount_tracking) {
    _exposureOptAddLine(
      `Tracking : ${metadata.mount_tracking}`,
      {indent:1}
    );
  }

  _exposureOptAddLine(
    `Focal length : ${
      metadata.focal_length_mm == null
        ? 'N/A'
        : `${metadata.focal_length_mm} mm`
    }`,
    {indent:1}
  );

  // ================================================================
  // ATMOSPHERIC ATTENUATION
  // ================================================================

  _exposureOptAddLine(
    'Atmospheric Attenuation',
    {section:true}
  );

  _exposureOptAddLine(
    `Status : ${atmos.enabled ? 'ON' : 'OFF'}`,
    {
      indent:1,
      success: Boolean(atmos.enabled),
      dim: !atmos.enabled
    }
  );

  if (
    atmos.sun_altitude_min_deg != null &&
    atmos.sun_altitude_max_deg != null
  ) {
    _exposureOptAddLine(
      `Sun altitude during eclipse : ` +
      `${_exposureOptFormat(atmos.sun_altitude_min_deg)}° → ` +
      `${_exposureOptFormat(atmos.sun_altitude_max_deg)}°`,
      {indent:1}
    );
  }

  rig.items.forEach(item => {
    const phase =
      labels[item.phase] ||
      item.phase ||
      'Preview';

    const altitude =
      item.sun_altitude_deg == null
        ? ''
        : ` — Sun ${_exposureOptFormat(item.sun_altitude_deg)}°`;

    _exposureOptAddLine(
      `${phase}${altitude}`,
      {subsection:true, indent:1}
    );

    if (!atmos.enabled) {
      _exposureOptAddLine(
        'No atmospheric correction',
        {indent:2, dim:true}
      );
      return;
    }

    if (item.phase !== 'partial') {
      _exposureOptAddLine(
        'Not applicable during Diamond Ring / Totality',
        {indent:2, dim:true}
      );
      return;
    }

    const added =
      Array.isArray(item.atmos_added_lines)
        ? item.atmos_added_lines
        : [];

    if (added.length === 0) {
      _exposureOptAddLine(
        'No exposure added',
        {indent:2, dim:true}
      );
    } else {
      const replaceAtmos = Boolean(
        document.getElementById('cfg-atmo-replace-switch')?.checked
      );
      _exposureOptAddLine(
        replaceAtmos
          ? 'Replaced by compensated exposures:'
          : 'Added compensated exposures:',
        {indent:2, success:true}
      );

      added.forEach(line => {
        _exposureOptAddLine(
          line,
          {indent:3, success:true}
        );
      });
    }
  });

  // ================================================================
  // ANTI-BLUR
  // ================================================================

  _exposureOptAddLine(
    'Anti-blur',
    {section:true}
  );

  _exposureOptAddLine(
    `Status : ${metadata.anti_trailing_enabled ? 'ON' : 'OFF'}`,
    {
      indent:1,
      success: Boolean(metadata.anti_trailing_enabled),
      dim: !metadata.anti_trailing_enabled
    }
  );

  if (metadata.motion_tolerance_px != null) {
    _exposureOptAddLine(
      `Motion tolerance : ${metadata.motion_tolerance_px} px`,
      {indent:1}
    );
  }

  const firstPolicy =
    rig.items.length
      ? rig.items[0].motion_policy
      : 'none';

  const policyLabels = {
    fixed_trailing: 'Solar trailing limit',
    field_rotation: 'Field rotation',
    none: 'No astronomical motion constraint'
  };

  _exposureOptAddLine(
    `Active method : ${policyLabels[firstPolicy] || firstPolicy}`,
    {indent:1}
  );

  rig.items.forEach(item => {
    const phase =
      labels[item.phase] ||
      item.phase ||
      'Preview';

    _exposureOptAddLine(
      phase,
      {subsection:true, indent:1}
    );

    if (item.error) {
      const message =
        typeof item.error === 'object'
          ? item.error.message
          : item.error;

      _exposureOptAddLine(
        `Error : ${message}`,
        {indent:2, error:true}
      );

      return;
    }

    if (item.motion_policy === 'none') {
      _exposureOptAddLine(
        'No anti-blur constraint',
        {indent:2, dim:true}
      );

      return;
    }

    _exposureOptAddLine(
      `Maximum exposure : ` +
      `${_exposureOptFormatCeiling(item.motion_ceiling_s)}`,
      {indent:2}
    );

    const changes =
      Array.isArray(item.anti_blur_diff_lines)
        ? item.anti_blur_diff_lines
        : [];

    if (changes.length === 0) {
      _exposureOptAddLine(
        'No impact',
        {indent:2, dim:true}
      );
    } else {
      _exposureOptAddLine(
        `Limiting factor : ${
          item.motion_policy === 'field_rotation'
            ? 'Field rotation'
            : 'Solar trailing'
        }`,
        {indent:2, warning:true}
      );

      changes.forEach(line => {
        _exposureOptAddLine(
          line,
          {indent:3, warning:true}
        );
      });
    }

    const corrections =
      Array.isArray(item.corrections)
        ? item.corrections
        : [];

    if (corrections.includes('iso_compensated')) {
      _exposureOptAddLine(
        'ISO compensation applied',
        {indent:2, success:true}
      );
    }

    if (corrections.includes('iso_rounded')) {
      _exposureOptAddLine(
        'ISO rounded to supported camera value',
        {indent:2}
      );
    }

    const warnings =
      Array.isArray(item.warnings)
        ? item.warnings
        : [];

    if (warnings.includes('iso_capped')) {
      _exposureOptAddLine(
        'ISO maximum reached',
        {indent:2, error:true}
      );
    }
  });

  if (options.separator !== false) {
    _exposureOptSeparator();
  }

  container.scrollTop = container.scrollHeight;
}

async function _fetchRigPreview(rigId, intents) {
  const currentRigPhoto = readRigPhotoConfig(rigId);

  const rigState = Array.isArray(rigDevicesState.rigs)
    ? rigDevicesState.rigs.find(
        rig => Number(rig.rig_id) === Number(rigId)
      )
    : null;

  const payload = {
    intents,
    rig_id: Number(rigId),
    rig_override: {
      optics: {
        focal_length_mm:
          rigState &&
          rigState.optics &&
          rigState.optics.focal_length_mm != null
            ? Number(rigState.optics.focal_length_mm)
            : null
      },
      photo: currentRigPhoto.photo
    }
  };

  const response = await fetch('/api/rigs/preview', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });

  const responseText = await response.text();

  if (!response.ok) {
    let message = responseText;

    try {
      const errorPayload = JSON.parse(responseText);
      message = errorPayload.error || responseText;
    } catch (_) {}

    throw new Error(
      message || `HTTP ${response.status}`
    );
  }

  return responseText;
}

async function requestRigPreviews(rigId, intents) {
  if (rigPreviewInFlight) return;

  const container = _exposureOptLogContainer();
  if (container) container.innerHTML = '';

  renderExposureOptHypotheses(intents);

  if (!_exposureOptRigIsActive(rigId)) {
    _exposureOptAddLine(`RIG ${rigId}`, {rig:true});
    _exposureOptAddLine(
      'Inactive — preview skipped',
      {indent:1, dim:true}
    );
    return;
  }

  if (!Array.isArray(intents) || intents.length === 0) {
    flash(
      'Configuration/circumstances incomplete',
      'red'
    );
    return;
  }

  const globalButton =
    document.getElementById(
      'btn-exposure-opt-preview-all'
    );

  rigPreviewInFlight = true;

  if (globalButton) {
    globalButton.disabled = true;
  }

  try {
    const responseText =
      await _fetchRigPreview(rigId, intents);

    renderExposureOptPreviewLog(
      responseText,
      rigId,
      {
        clear:false,
        separator:false
      }
    );

  } catch (error) {
    _exposureOptAddLine(`RIG ${rigId}`, {rig:true});

    _exposureOptAddLine(
      `Preview error : ${error.message}`,
      {indent:1, error:true}
    );

  } finally {
    rigPreviewInFlight = false;

    if (globalButton) {
      globalButton.disabled = false;
    }
  }
}

async function requestAllRigPreviews() {
  if (rigPreviewInFlight) return;

  const intents = buildPreviewIntents();
  const container = _exposureOptLogContainer();

  if (container) {
    container.innerHTML = '';
  }

  renderExposureOptHypotheses(intents);

  if (!Array.isArray(intents) || intents.length === 0) {
    flash(
      'Configuration/circumstances incomplete',
      'red'
    );
    return;
  }

  const globalButton =
    document.getElementById(
      'btn-exposure-opt-preview-all'
    );

  rigPreviewInFlight = true;

  if (globalButton) {
    globalButton.disabled = true;
  }

  try {
    for (let rigId = 1; rigId <= 4; rigId += 1) {

      if (!_exposureOptRigIsActive(rigId)) {
        _exposureOptAddLine(
          `RIG ${rigId}`,
          {rig:true}
        );

        _exposureOptAddLine(
          'Inactive — preview skipped',
          {indent:1, dim:true}
        );

        _exposureOptSeparator();
        continue;
      }

      try {
        const responseText =
          await _fetchRigPreview(
            rigId,
            intents
          );

        renderExposureOptPreviewLog(
          responseText,
          rigId,
          {
            clear:false,
            separator:true
          }
        );

      } catch (error) {
        _exposureOptAddLine(
          `RIG ${rigId}`,
          {rig:true}
        );

        _exposureOptAddLine(
          `Preview error : ${error.message}`,
          {indent:1, error:true}
        );

        _exposureOptSeparator();
      }
    }

  } finally {
    rigPreviewInFlight = false;

    if (globalButton) {
      globalButton.disabled = false;
    }
  }

  if (container) {
    container.scrollTop = 0;
  }
}

async function saveCameraConfig() {
  const sel = document.getElementById('camera-config-select');
  const current = sel && sel.value
    ? sel.value.replace(/^photo_/, '').replace(/\.json$/, '')
    : 'photo_setup';

  const name = prompt('Photo Setup file name:', current);
  if (!name) return;

  const data = _readCameraConfig();

  const save = async overwrite => {
    return fetch('/api/configs/save_photo', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({filename: name, data, overwrite})
    });
  };

  try {
    let r = await save(false);
    let d = await r.json();

    if (r.status === 409) {
      if (!confirm(`${d.filename || name} already exists. Overwrite it?`)) return;
      r = await save(true);
      d = await r.json();
    }

    if (r.ok && d.status === 'ok') {
      flash('Saved: ' + d.filename, 'green');
      await loadCameraConfigList();
      const select = document.getElementById('camera-config-select');
      if (select) select.value = d.filename;
    } else {
      flash(d.error || 'Error', 'red');
    }
  } catch(e) {
    flash('Network error', 'red');
  }
}

async function loadConfigFile(filename) {
  if (!filename) return;
  try {
    const r = await fetch('/api/configs/load/' + encodeURIComponent(filename));
    if (!r.ok) { flash('Failed to load configuration', 'red'); return; }
    const data = await r.json();
    const rPost = await fetch('/api/eclipse/override', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await rPost.json();
    if (d.status === 'ok') flash('Configuration loaded: ' + filename, 'green');
    else flash(d.error || 'Error', 'red');
  } catch(e) { flash('Network error', 'red'); }
}

async function promptSaveConfig() {
  const name = prompt('Configuration file name:', 'ma_config');
  if (!name) return;
  await saveOverrides();
  try {
    const r = await fetch('/api/configs/save', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ filename: name })
    });
    const d = await r.json();
    if (d.status === 'ok') { flash('Saved: ' + d.filename, 'green'); loadConfigFileList(); }
    else flash(d.error || 'Error', 'red');
  } catch(e) { flash('Network error', 'red'); }
}

async function refreshSavedCircumstances() {
  const select = document.getElementById('eclipse-circumstances-select');
  if (!select) return;

  try {
    const response = await fetch('/api/configs/list_eclipse');
    if (!response.ok) return;

    const data = await response.json();
    const files = data.files || [];
    const currentValue = select.value;

    select.innerHTML = '<option value="">— Circumstances file —</option>';

    files.forEach(file => {
      if (!file || !file.name || file.name === 'todayeclipse.json') return;

      const option = document.createElement('option');
      option.value = file.name;
      option.textContent = file.name;
      select.appendChild(option);
    });

    if ([...select.options].some(option => option.value === currentValue)) {
      select.value = currentValue;
    }
  } catch (_error) {
  }
}



function applyCircumstancesLocationToForm(eclipseData) {
  if (!eclipseData || typeof eclipseData !== 'object') return;

  const location = eclipseData._circumstances_location;
  if (!location || typeof location !== 'object') return;

  const latitude = location.latitude ?? location.lat;
  const longitude = location.longitude ?? location.lon ?? location.lng;
  const altitude = location.altitude_m ?? location.altitude ?? location.alt;

  const latInput = document.getElementById('inp-lat');
  const lonInput = document.getElementById('inp-lon');
  const altInput = document.getElementById('inp-alt');

  if (latInput && latitude != null) latInput.value = latitude;
  if (lonInput && longitude != null) lonInput.value = longitude;
  if (altInput && altitude != null) altInput.value = altitude;
}


async function loadSavedCircumstances(filename) {
  if (!filename) return;

  try {
    const response = await fetch('/api/trigger/select', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        filename: filename,
        dir: 'circumstances'
      })
    });

    const data = await response.json();

    if (!response.ok || data.status !== 'ok') {
      flash(data.error || 'Failed to load circumstances.', 'red');
      return;
    }

    const eclipseData = data.data || {};

    state.eclipse = eclipseData;
    updateEclipseSaveFilename(eclipseData);

    if (eclipseData._timezone) {
      _gpsTimezone = eclipseData._timezone;
    }

    renderContacts(eclipseData);
    populateOverrides(eclipseData);
    applyCircumstancesLocationToForm(eclipseData);

    flash('Circumstances loaded: ' + filename, 'green');
  } catch (_error) {
    flash('Network error.', 'red');
  }
}

async function saveEclipseConfig() {
  const activePrefix = typeof _eclipseSavePrefix === 'string'
    && /^\d{8}_Circumstances_$/.test(_eclipseSavePrefix)
    ? _eclipseSavePrefix
    : '';

  const filename = prompt(
    'Circumstances file name:',
    activePrefix
  );

  if (!filename || !filename.trim() || filename.trim() === activePrefix) {
    return;
  }

  const save = overwrite => fetch('/api/configs/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(overwrite ? {filename, overwrite: true} : {filename})
  });

  try {
    let response = await save(false);
    if (response.status === 409) {
      if (!confirm('File exists. Overwrite?')) return;
      response = await save(true);
    }
    const data = await response.json();
    if (response.ok && data.status === 'ok') {
      flash('Saved as ' + data.filename, 'green');
      loadEclipseFileList();
      await refreshSavedCircumstances();

      const circumstancesSelect = document.getElementById(
        'eclipse-circumstances-select'
      );
      if (circumstancesSelect && data.filename) {
        circumstancesSelect.value = data.filename;
      }
    } else {
      flash(data.error || 'Save failed.', 'red');
    }
  } catch(e) {
    flash('Network error.', 'red');
  }
}

async function cleanCircumstances() {
  if (!confirm('Delete all saved circumstances files?')) {
    return;
  }

  try {
    const cleanResponse = await fetch('/api/configs/circumstances/clean', { method: 'POST' });
    if (!cleanResponse.ok) {
      alert('Clean failed.');
      return;
    }

    const listResponse = await fetch('/api/configs/list_eclipse');
    if (!listResponse.ok) {
      alert('Refresh failed.');
      return;
    }
    const data = await listResponse.json();
    const files = data.files || [];

    const triggerSelect = document.getElementById('trigger-config-select');
    if (triggerSelect) {
      triggerSelect.innerHTML = '';
      files.forEach(file => {
        const option = document.createElement('option');
        option.value = file.name;
        option.dataset.dir = file.dir;
        option.textContent = file.name;
        triggerSelect.appendChild(option);
      });
      triggerSelect.selectedIndex = -1;
    }

    const eclipseSelect = document.getElementById('eclipse-file-select');
    if (eclipseSelect) {
      eclipseSelect.innerHTML = '<option value="">— Circumstances file —</option>';
      files.forEach(file => {
        const option = document.createElement('option');
        option.value = file.name;
        option.textContent = file.name;
        eclipseSelect.appendChild(option);
      });
    }
  await refreshSavedCircumstances();
  } catch(e) {
    alert('Clean failed.');
  }
}

async function cleanCameraConfigs() {
  if (!confirm(
    'Delete ALL saved Photo Setup JSON files?\n\nThis cannot be undone.'
  )) return;

  try {
    const response = await fetch('/api/configs/photo_cfg/clean', {
      method: 'POST'
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `HTTP error ${response.status}`);
    }

    flash(`${data.deleted || 0} Photo Setup file(s) deleted`, 'yellow');
    await loadCameraConfigList();

  } catch(e) {
    flash(`Photo Setup CLEAN: ${e.message}`, 'red');
  }
}

// Charger les listes au démarrage — isolé pour ne pas bloquer en cas d'erreur
try { loadConfigFileList(); } catch(e) { console.error('loadConfigFileList', e); }
try { loadCameraConfigList(); } catch(e) { console.error('loadCameraConfigList', e); }
try { loadEclipseFileList(); } catch(e) { console.error('loadEclipseFileList', e); }
try { loadTriggerConfigList(); } catch(e) { console.error('loadTriggerConfigList', e); }
try { refreshSavedCircumstances(); } catch(e) { console.error('refreshSavedCircumstances', e); }

function appendLog(text, level = 'info', source = '', ts = '') {
  if (_logPaused) return;

  // Trouver le conteneur approprié selon la source
  let containerId = 'log-container';
  if (source === 'gps_sync') containerId = 'log-container-gps_sync';
  else if (source === 'calculator') containerId = 'log-container-calculator';
  else if (source === 'trigger') containerId = 'log-container-trigger';
  else if (source === 'exposure_opt') containerId = 'log-container-exposure_opt';

  const containers = document.querySelectorAll(`#${containerId}`);
  if (containers.length === 0) return;

  const d = document.createElement('div');
  d.className = `log-line ${level}`;
  d.textContent = ts ? `[${ts}] ${text}` : text;

  containers.forEach(c => {
    c.appendChild(d.cloneNode(true));

    const maxLines = 600;

    while (c.children.length > maxLines) {
      c.removeChild(c.firstChild);
    }

    c.scrollTop = c.scrollHeight;
  });
}

function appendExposureOptLog(text, level = 'info') {
  const now = new Date();
  const ts = [
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0')
  ].join(':');
  appendLog(text, level, 'exposure_opt', ts);
}

function appendCalcLog(text, level = 'info') {
  const c = document.getElementById('calc-log');
  if (!c) return;  // Déjà géré si l'élément n'existe pas
  const d = document.createElement('div');
  d.style.cssText = 'padding:1px 0;font-size:11px;line-height:1.5;';
  d.style.color = level === 'error' ? '#ff4c4c' : level === 'success' ? '#3ddc84' : '#5a7a9a';
  d.textContent = text;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

let _logPaused = false;
async function clearLog(source = '') {
  // Vider l'affichage du conteneur approprié
  let containerId = 'log-container';
  if (source === 'gps_sync') containerId = 'log-container-gps_sync';
  else if (source === 'calculator') containerId = 'log-container-calculator';
  else if (source === 'trigger') containerId = 'log-container-trigger';
  else if (source === 'exposure_opt') containerId = 'log-container-exposure_opt';

  document.querySelectorAll(`#${containerId}`).forEach(c => c.innerHTML = '');
  // Bloquer l'ajout de nouvelles lignes pendant 1s
  _logPaused = true;
  setTimeout(() => { _logPaused = false; }, 1000);
}


async function erasePersistentDataAndReboot() {
  const confirmed = confirm(
    'WARNING\n\n'
    + 'This will permanently erase ALL persistent user data and reboot the Raspberry Pi.\n\n'
    + 'Saved RIG assignments, eclipse circumstances, camera configurations, '
    + 'GPS state and other persisted runtime settings will be lost.\n\n'
    + 'Continue?'
  );

  if (!confirmed) return;

  const button = document.getElementById('erase-persistent-data-reboot');

  if (button) {
    button.disabled = true;
    button.textContent = '⚠ ERASING DATA — REBOOTING… ⚠';
  }

  try {
    const response = await fetch('/api/system/erase-persistent-data-and-reboot', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        confirmation: 'ERASE ALL PERSISTANT DATA & REBOOT'
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `HTTP error ${response.status}`);
    }

    flash('Persistent data erased. Raspberry Pi rebooting…', 'yellow');
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = '⚠ ERASE ALL PERSISTANT DATA & REBOOT ⚠';
    }
    flash(`Reset failed: ${error.message}`, 'red');
  }
}


// ════════════════════════════════════════════════════════════════
// NAVIGATION
// ════════════════════════════════════════════════════════════════
function showTab(n) {
  document.querySelectorAll('#tabs > .tab').forEach(t => t.classList.toggle('active', Number(t.dataset.pageIndex) === n));
  const pageIds = ['devices-panel', 'page-0', 'page-1', 'page-2', 'page-exposure-opt', 'retired-page-5', 'page-3', 'controls-panel', 'page-4', 'add-camera-panel', 'debug-panel'];
  document.querySelectorAll('#pages > .page').forEach(p => p.classList.toggle('active', p.id === pageIds[n]));
  state.currentPage = n;
  if (n === 3) {
    loadCameraConfigList();
    // Forcer ISO 100 par défaut (Chromium ignore selected sur éléments cachés)
    const pi = document.getElementById('cfg-partial-iso');
    const di = document.getElementById('cfg-dr-iso');
    const ti = document.getElementById('cfg-tot-iso');
    if (pi && pi.value === '') pi.value = '100';
    if (di && di.value === '') di.value = '100';
    if (ti && ti.value === '') ti.value = '100';
  }
  if (n === 4) {
    loadExposureOptConfigList();
    loadRigPhotoConfig();
  }
  if (n === 5) {
    loadSequencerConfigLists().then(() => {
      renderSequencer();
    });
  }
  if (n === 8) {
    loadTriggerConfigList();
    loadEclipseFileList();
  }
  if (n === 10) {
    Promise.resolve(loadTriggerConfigList())
      .then(() => syncDebugUiFromTrigger());
    loadEclipseFileList();
  }
}

// ════════════════════════════════════════════════════════════════
// FLASH
// ════════════════════════════════════════════════════════════════
let _flashTimer = null;
function flash(msg, type = 'info') {
  const el = document.getElementById('flash');
  el.textContent = msg;
  el.style.borderColor = type === 'green' ? 'var(--green)'
    : type === 'red' ? 'var(--red)'
    : type === 'yellow' ? 'var(--yellow)'
    : type === 'blue' ? 'var(--blue)'
    : 'var(--accent)';
  el.classList.add('show');
  clearTimeout(_flashTimer);
  _flashTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

// ════════════════════════════════════════════════════════════════
// INIT + POLLING
// ════════════════════════════════════════════════════════════════
async function loadEclipseData() {
  try {
    const r = await fetch('/api/eclipse/current');
    if (r.ok) {
      const d = await r.json();
      if (!d.error) {
        state.eclipse = d;
        updateEclipseSaveFilename(d);
        if (d._timezone) _gpsTimezone = d._timezone;
        renderContacts(d);
        populateOverrides(d);
        applyCircumstancesLocationToForm(d);
      }
    }
  } catch(e) {}
}

async function loadCameraStatus() {
  try {
    const r = await fetch('/api/status');
    if (r.ok) {
      const d = await r.json();
      const cam = d.camera || {};
      const camStatus = document.getElementById('cam-status');
      const camBattery = document.getElementById('cam-battery');
      if (camStatus) {
        camStatus.textContent = cam.connected ? '✅ Connected' : '❌ Not detected';
        camStatus.className = `stat-value ${cam.connected ? 'green' : 'red'}`;
      }
      if (camBattery) camBattery.textContent = cam.battery || '--';
      updateCameraTimeSync(cam, d.gps || {});
      // Mettre à jour la barre batterie dans l'onglet trigger
      if (cam.battery) {
        const pct = parseInt(cam.battery);
        if (!isNaN(pct)) updateBattery(pct);
      }
    }
  } catch(e) {}
}

// Countdown toutes les secondes
setInterval(() => { if (state.eclipse) updateCountdowns(state.eclipse); }, 1000);
// Camera toutes les 10s
setInterval(loadCameraStatus, 10000);

// Init

// Tous les RIGs doivent être entièrement visibles immédiatement.
// Le backend remplacera ensuite cet état provisoire par la configuration persistée.
renderRigDevices({
  rigs: DEFAULT_RIGS,
  inventory: {
    camera: [],
    focuser: [],
    mount: [],
  },
});

// Le sélecteur GPS doit exister immédiatement, même avant toute réponse backend.
renderDevices({
  gps: {
    plugin: 'none',
    active: false,
    detected: false,
    suggested_plugin: null,
  },
});

loadSupportedEclipses();
loadEclipseData();
loadCameraStatus();

// ════════════════════════════════════════════════════════════════
// CAMERA VALIDATION — end-to-end real camera run
// ════════════════════════════════════════════════════════════════
let cameraValidationPolling = false;
let cameraValidationQuestionId = null;
let cameraValidationLastResultId = null;
let cameraValidationStarting = false;
let cameraValidationStartingMessage = '';

function formatValidationDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes ? `${minutes} min ${String(rest).padStart(2, '0')} s` : `${rest} s`;
}

function renderCameraValidationStatus(status) {
  const summary = document.getElementById('camera-validation-summary');
  const question = document.getElementById('camera-validation-question');
  const prompt = document.getElementById('camera-validation-prompt');
  const deleteButton = document.getElementById('camera-validation-delete-files');
  if (!summary || !question || !prompt || !deleteButton) return;

  window.cameraValidationRunning = Boolean(status.running);
  const validationLogs = Array.isArray(status.logs) ? status.logs.slice() : [];
  if (cameraValidationStarting && cameraValidationStartingMessage &&
      !validationLogs.includes(cameraValidationStartingMessage)) {
    validationLogs.unshift(cameraValidationStartingMessage);
  }
  updateCameraAddLog('validation', validationLogs);

  const result = status.result;
  if (status.running) {
    summary.textContent = `Validation running — phase: ${status.phase || 'running'}`;
  } else if (result && result.analysis) {
    const analysis = result.analysis;
    const timing = analysis.timing || {};
    const timingText = Number.isFinite(timing.stddev_ms)
      ? ` · σ=${timing.stddev_ms.toFixed(1)} ms · max|Δ|=${Number(timing.max_abs_ms || 0).toFixed(1)} ms` : '';
    const countText = analysis.actual_count_complete === false
      ? `${analysis.confirmed_photos}/${analysis.expected_photos} confirmed minimum`
      : `${analysis.confirmed_photos}/${analysis.expected_photos} confirmed`;
    summary.textContent = `${analysis.verdict} — ${countText}${timingText}`;
    if (!validationLogs.some(line => /\b(PASS|FAIL)\b/i.test(String(line)))) {
      appendCameraAddLogLine('validation', `${analysis.verdict}: ${countText}${timingText}`);
    }
  } else {
    summary.textContent = '';
  }

  const q = status.question;
  cameraValidationQuestionId = q ? q.id : null;
  question.hidden = !q;
  prompt.textContent = q ? q.message : '';
  const fail = Boolean(result && result.analysis && result.analysis.verdict === 'FAIL');
  deleteButton.hidden = !fail;
  if (result && result.validation_id) cameraValidationLastResultId = result.validation_id;

  const cancel = document.getElementById('camera-characterization-cancel');
  if (cancel) cancel.disabled = !status.running;
  const select = document.getElementById('camera-characterization-select');
  const start = document.getElementById('camera-characterization-start');
  if (!status.running && !cameraCharacterizationStarting) {
    if (select) select.disabled = false;
    if (start) start.disabled = !select || !select.options.length;
  }
}

async function pollCameraValidation() {
  if (cameraValidationPolling) return;
  cameraValidationPolling = true;
  try {
    const response = await fetch('/api/camera-validation');
    const status = await cameraJsonResponse(response, 'Camera validation status');
    if (!response.ok) throw new Error(status.error || `HTTP ${response.status}`);
    renderCameraValidationStatus(status);
  } catch (error) {
    const summary = document.getElementById('camera-validation-summary');
    if (summary) summary.textContent = `Validation status unavailable: ${error.message}`;
  } finally {
    cameraValidationPolling = false;
  }
}

async function startAutomaticCameraValidation(locator) {
  cameraValidationStarting = true;
  cameraValidationStartingMessage = 'Characterization successful — starting camera validation automatically…';
  appendCameraAddLogLine('validation', cameraValidationStartingMessage);
  try {
    const response = await fetch('/api/camera-validation/prepare', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({locator}),
    });
    const prepared = await cameraJsonResponse(response, 'Camera validation prepare');
    if (!response.ok) throw new Error(prepared.error || `HTTP ${response.status}`);
    const startResponse = await fetch('/api/camera-validation/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: prepared.token}),
    });
    const started = await cameraJsonResponse(startResponse, 'Camera validation start');
    if (!startResponse.ok) throw new Error(started.error || `HTTP ${startResponse.status}`);
    appendCameraAddLogLine('validation', `SUCCESS: Validation started — ${prepared.expected_photos} photos expected`);
    flash(`Camera validation started — ${prepared.expected_photos} photos expected`, 'green');
  } catch (error) {
    appendCameraAddLogLine('validation', `FAILED: Automatic validation: ${error.message}`);
    flash(`Camera validation: ${error.message}`, 'red');
  } finally {
    cameraValidationStarting = false;
    cameraValidationStartingMessage = '';
    await pollCameraValidation();
  }
}

async function prepareCameraValidation() {
  const select = document.getElementById('camera-validation-select');
  const start = document.getElementById('camera-validation-start');
  if (!select || !select.value) {
    flash('Select a characterized camera first.', 'red');
    return;
  }

  cameraValidationStarting = true;
  cameraValidationStartingMessage = 'Preparing camera validation…';
  select.disabled = true;
  if (start) start.disabled = true;
  appendCameraAddLogLine(
    'validation',
    cameraValidationStartingMessage
  );
  await waitForBrowserPaint();

  try {
    const response = await fetch('/api/camera-validation/prepare', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({locator: select.value}),
    });
    const prepared = await cameraJsonResponse(response, 'Camera validation prepare');
    if (!response.ok) throw new Error(prepared.error || `HTTP ${response.status}`);

    const bracketText = Array.isArray(prepared.supported_bracket_frames) && prepared.supported_bracket_frames.length
      ? prepared.supported_bracket_frames.join('/')
      : 'none';
    const authorized = confirm(
      'REAL CAMERA VALIDATION\n\n' +
      `Camera: ${(prepared.camera && prepared.camera.manufacturer) || ''} ${(prepared.camera && prepared.camera.model) || ''}\n` +
      `Expected photos: ${prepared.expected_photos}\n` +
      `Estimated total duration: ${formatValidationDuration(prepared.estimated_duration_s)}\n` +
      `Native brackets exercised: ${bracketText}\n\n` +
      'The real camera scheduler, IPC and camera workers will be used.\n' +
      'Camera settings will change and real RAW photos will be written to the card.\n\n' +
      'Authorize validation now?'
    );

    if (!authorized) {
      cameraValidationStarting = false;
      cameraValidationStartingMessage = '';
      await pollCameraValidation();
      return;
    }

    const startResponse = await fetch('/api/camera-validation/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: prepared.token}),
    });
    const started = await cameraJsonResponse(startResponse, 'Camera validation start');
    if (!startResponse.ok) throw new Error(started.error || `HTTP ${startResponse.status}`);
    flash(`Camera validation started — ${prepared.expected_photos} photos expected`, 'green');
    await pollCameraValidation();
    cameraValidationStarting = false;
    cameraValidationStartingMessage = '';
  } catch (error) {
    cameraValidationStarting = false;
    cameraValidationStartingMessage = '';
    await pollCameraValidation();
    flash(`Camera validation: ${error.message}`, 'red');
  }
}

async function cancelCameraValidation() {
  try {
    const response = await fetch('/api/camera-validation/cancel', {method: 'POST'});
    const data = await cameraJsonResponse(response, 'Camera validation cancel');
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    await pollCameraValidation();
  } catch (error) {
    flash(`Camera validation cancel: ${error.message}`, 'red');
  }
}

async function answerCameraValidation(outcome) {
  if (!cameraValidationQuestionId) return;
  try {
    const response = await fetch('/api/camera-validation/answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question_id: cameraValidationQuestionId, outcome}),
    });
    const data = await cameraJsonResponse(response, 'Camera validation answer');
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    cameraValidationQuestionId = null;
    await pollCameraValidation();
  } catch (error) {
    flash(`Camera validation confirmation: ${error.message}`, 'red');
  }
}

async function deleteFailedCameraValidationFiles() {
  if (!cameraValidationLastResultId) return;
  if (!confirm(
    'DELETE THE GENERATED CAMERA PROFILE AND TIMING FILES?\n\n' +
    'Only the exact profile/timing files associated with this failed validation will be deleted.\n' +
    'The validation report and run log will be kept for debugging.\n\n' +
    'This action cannot be undone.'
  )) return;

  try {
    const response = await fetch('/api/camera-validation/delete-files', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm: true}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    flash(`Deleted ${Array.isArray(data.deleted) ? data.deleted.length : 0} generated camera file(s)`, 'yellow');
    await pollCameraValidation();
    await pollCameraCharacterization();
  } catch (error) {
    flash(`Camera validation delete: ${error.message}`, 'red');
  }
}

// END CAMERA VALIDATION


// ════════════════════════════════════════════════════════════════
// DEBUG TAB — UI adapter over the existing Trigger functionality
// ════════════════════════════════════════════════════════════════

function copySelectState(sourceId, targetId) {
  const source = document.getElementById(sourceId);
  const target = document.getElementById(targetId);

  if (!source || !target) return;

  const previous = target.value;
  target.innerHTML = source.innerHTML;

  if (Array.from(target.options).some(option => option.value === source.value)) {
    target.value = source.value;
  } else if (
    previous
    && Array.from(target.options).some(option => option.value === previous)
  ) {
    target.value = previous;
  }
}


function syncDebugRigSelection() {
  for (let rigId = 1; rigId <= 4; rigId += 1) {
    const source = document.getElementById(`trigger-rig-${rigId}`);
    const target = document.getElementById(`debug-rig-${rigId}`);

    if (!source || !target) continue;

    target.hidden = source.hidden;
    target.disabled = source.disabled;
    target.classList.toggle(
      'active',
      source.classList.contains('active')
    );
  }

  const triggerTarget = document.getElementById('trigger-target-label');
  const debugTarget = document.getElementById('debug-target-label');

  if (triggerTarget && debugTarget) {
    debugTarget.textContent = triggerTarget.textContent;
  }
}


function syncDebugLog() {
  const source = document.getElementById('log-container-trigger');
  const target = document.getElementById('log-container-debug');

  if (source && target) {
    const followTail = _logNearBottom(target);
    const previousScrollTop = target.scrollTop;

    target.innerHTML = source.innerHTML;

    if (followTail) {
      target.scrollTop = target.scrollHeight;
    } else {
      target.scrollTop = Math.min(
        previousScrollTop,
        Math.max(0, target.scrollHeight - target.clientHeight)
      );
    }
  }

  syncDebugLogTitle();
}


function syncDebugLogTitle() {
  const sourceTitle = document.getElementById('trigger-log-title');
  const targetTitle = document.getElementById('debug-log-title');

  if (sourceTitle && targetTitle) {
    targetTitle.textContent = sourceTitle.textContent.replace(
      /^Trigger log/,
      'Debug log'
    );
  }
}


function syncDebugCircumstances() {
  const contacts = document.getElementById('trigger-contacts');
  const debugContacts = document.getElementById('debug-contacts');

  if (contacts && debugContacts) {
    debugContacts.innerHTML = contacts.innerHTML;
  }

  const mappings = [
    ['trig-eclipse-type2', 'debug-eclipse-type'],
    ['trig-eclipse-type-gps', 'debug-eclipse-type-gps']
  ];

  mappings.forEach(([sourceId, targetId]) => {
    const source = document.getElementById(sourceId);
    const target = document.getElementById(targetId);

    if (!source || !target) return;

    target.textContent = source.textContent;
    target.style.color = source.style.color;
  });
}


function syncDebugActionState() {
  const mappings = [
    ['btn-start', 'btn-debug-start'],
    ['btn-totality-only', 'btn-debug-totality-only'],
    ['btn-stop', 'btn-debug-stop']
  ];

  mappings.forEach(([sourceId, targetId]) => {
    const source = document.getElementById(sourceId);
    const target = document.getElementById(targetId);

    if (!source || !target) return;

    target.disabled = source.disabled;
  });
}


function syncDebugUiFromTrigger() {
  copySelectState(
    'trigger-circumstances-select',
    'debug-circumstances-select'
  );
  copySelectState(
    'trigger-photo-select',
    'debug-photo-select'
  );
  copySelectState(
    'trigger-exposure-opt-select',
    'debug-exposure-opt-select'
  );

  syncDebugCircumstances();
  syncDebugRigSelection();
  syncDebugLog();
  syncDebugActionState();
}


async function setDebugTriggerInput(kind, value) {
  const mapping = {
    circumstances: 'trigger-circumstances-select',
    photo: 'trigger-photo-select',
    exposure_opt: 'trigger-exposure-opt-select'
  };

  const sourceId = mapping[kind];
  const source = sourceId
    ? document.getElementById(sourceId)
    : null;

  if (!source) return;

  source.value = value;

  if (kind === 'circumstances') {
    await loadTriggerCircumstances(value);
  } else if (kind === 'photo') {
    await refreshTriggerCircumstancesForPhoto();
  }

  syncDebugUiFromTrigger();
}


function syncTriggerInputsFromDebug() {
  const mappings = [
    ['debug-circumstances-select', 'trigger-circumstances-select'],
    ['debug-photo-select', 'trigger-photo-select'],
    ['debug-exposure-opt-select', 'trigger-exposure-opt-select']
  ];

  mappings.forEach(([debugId, triggerId]) => {
    const debugControl = document.getElementById(debugId);
    const triggerControl = document.getElementById(triggerId);

    if (debugControl && triggerControl) {
      triggerControl.value = debugControl.value;
    }
  });
}


function selectDebugTriggerRig(rigId) {
  selectTriggerRig(rigId);
  syncDebugUiFromTrigger();
}


async function startDebugFromDebugTab() {
  syncTriggerInputsFromDebug();
  await startDebug();
  syncDebugUiFromTrigger();
}


async function startDryRunFromDebugTab() {
  syncTriggerInputsFromDebug();
  await startDryRun();
  syncDebugUiFromTrigger();
}


async function startTriggerFromDebugTab() {
  syncTriggerInputsFromDebug();
  await startTrigger();
  syncDebugUiFromTrigger();
}


async function startTotalityOnlyFromDebugTab() {
  syncTriggerInputsFromDebug();
  await startTotalityOnly();
  syncDebugUiFromTrigger();
}


async function stopTriggerFromDebugTab() {
  await stopTrigger();
  syncDebugUiFromTrigger();
}


async function cleanDebugGeneratedFiles() {
  if (typeof anyActiveTriggerRunning === 'function' && anyActiveTriggerRunning()) {
    flash('Stop the active Trigger/Debug run before CLEAN.', 'red');
    return;
  }

  if (!confirm(
    'Delete all generated DEBUG circumstances files?\n\n'
    + 'Reference eclipse circumstances files are not affected.'
  )) {
    return;
  }

  try {
    const response = await fetch('/api/trigger/debug/clean', {
      method: 'POST'
    });

    const payload = await response.json();

    if (!response.ok) {
      throw new Error(
        payload.error || `HTTP error ${response.status}`
      );
    }

    flash(
      `${payload.deleted || 0} generated DEBUG file(s) deleted`,
      'yellow'
    );

    await loadTriggerConfigList();
    syncDebugUiFromTrigger();

  } catch (error) {
    flash(`DEBUG CLEAN: ${error.message}`, 'red');
  }
}


function _observeDebugMirror(id, callback, options = {}) {
  const node = document.getElementById(id);
  if (!node) return;

  const observer = new MutationObserver(callback);
  observer.observe(node, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    ...options
  });
}


function installDebugUiMirror() {
  _observeDebugMirror('trigger-contacts', syncDebugCircumstances);
  _observeDebugMirror('trig-eclipse-type2', syncDebugCircumstances);
  _observeDebugMirror('trig-eclipse-type-gps', syncDebugCircumstances);

  _observeDebugMirror('trigger-target-label', syncDebugRigSelection);
  _observeDebugMirror('trigger-log-title', syncDebugLogTitle);

  _observeDebugMirror(
    'log-container-trigger',
    syncDebugLog,
    {attributes: false}
  );

  _observeDebugMirror('btn-start', syncDebugActionState);
  _observeDebugMirror('btn-totality-only', syncDebugActionState);
  _observeDebugMirror('btn-stop', syncDebugActionState);

  syncDebugUiFromTrigger();
}


if (document.readyState === 'loading') {
  document.addEventListener(
    'DOMContentLoaded',
    installDebugUiMirror,
    {once: true}
  );
} else {
  installDebugUiMirror();
}

// ════════════════════════════════════════════════════════════════
// CAMERA RE-CHARACTERIZATION / SYSTEM MAINTENANCE
// ════════════════════════════════════════════════════════════════
async function startCameraRecharacterization() {
  const select = document.getElementById('camera-recharacterization-select');
  const button = document.getElementById('camera-recharacterization-start');
  const locator = select && select.value;

  if (
    !locator ||
    !confirm(
      'Re-characterize this camera?\n\n' +
      'A complete new characterization will run. ' +
      'The current profile remains active unless the new run succeeds.'
    )
  ) return;

  // Remove the stale completed result before starting the new job.
  cameraAddLogState.characterization = [];
  cameraAddLogState.characterizationOffset = 0;
  cameraAddLogState.characterizationResult = '';
  cameraAddLogState.clearedCharacterizationResult = '';
  renderCameraAddLog();

  cameraCharacterizationStarting = true;
  cameraCharacterizationStartingMessage =
    'Starting camera re-characterization…';
  if (select) select.disabled = true;
  if (button) button.disabled = true;
  appendCameraAddLogLine(
    'characterization',
    cameraCharacterizationStartingMessage
  );
  await waitForBrowserPaint();

  try {
    const response = await fetch(
      '/api/camera-characterization/recharacterize',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({locator})
      }
    );
    const data = await response.json();
    if (!response.ok) {
      throw new Error(
        data.error || `HTTP ${response.status}`
      );
    }

    cameraCharacterizationStarting = false;
    cameraCharacterizationStartingMessage = '';
    flash('Camera re-characterization started', 'green');
  } catch (error) {
    cameraCharacterizationStarting = false;
    cameraCharacterizationStartingMessage = '';
    if (select) select.disabled = false;
    if (button) button.disabled = !locator;
    flash(error.message || 'Re-characterization failed to start', 'red');
  }
}
async function maintenancePost(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function renderInstalledSolarTriggerReleases(releaseState) {
  const select = document.getElementById('solartrigger-rollback-version');
  const button = document.getElementById('solartrigger-rollback-release');
  if (!select || !button) return;

  const previous = select.value;
  const active = releaseState && releaseState.active;
  const releases = Array.isArray(releaseState && releaseState.releases)
    ? releaseState.releases
    : [];

  select.innerHTML = '<option value="">— Installed version —</option>';
  releases
    .filter(item => item && !item.active && item.rollback_eligible !== false)
    .forEach(item => {
      const option = document.createElement('option');
      option.value = String(item.version || '');
      option.textContent =
        `${item.version || item.directory}${item.build_commit ? ' · ' + String(item.build_commit).slice(0, 8) : ''}`;
      select.appendChild(option);
    });

  if ([...select.options].some(option => option.value === previous)) {
    select.value = previous;
  }
  select.disabled = releases.length <= 1;
  button.disabled = !select.value;

  const status = document.getElementById('solartrigger-update-status');
  if (status && active) {
    status.textContent = `Active release: ${active}`;
  }

  select.onchange = () => {
    button.disabled = !select.value;
  };
}

async function loadMaintenanceStatus(){
  const button = document.getElementById('system-check-update');
  if (!button) return;

  try {
    const response = await fetch('/api/system/maintenance/status');
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.error || `HTTP ${response.status}`
      );
    }

    const ethernetConnected = Boolean(
      data.ethernet && data.ethernet.connected
    );

    button.disabled = !ethernetConnected;
    button.textContent = ethernetConnected
      ? 'CHECK AND UPDATE SYSTEM'
      : 'CHECK AND UPDATE SYSTEM — Eth need to be connected';

    const lines = Array.isArray(data.logs)
      ? data.logs.map(String)
      : [];

    if (data.kind && data.kind.startsWith('apt-')) {
      updateCameraAddLog('systemUpdate', lines);
    } else if (data.kind) {
      updateCameraAddLog('solarTriggerUpdate', lines);
    }

    renderInstalledSolarTriggerReleases(data.release_state || {});
  } catch (error) {
    console.warn(
      'Unable to load maintenance status:',
      error
    );
  }
}
async function checkAndUpdateSystem(){
  if(!confirm(
    'Check for system updates and install them now?\n\n'
    + 'This runs apt-get update + apt-get upgrade -y.'
  )) return;

  cameraAddLogState.systemUpdate = [
    'Starting system update...'
  ];
  cameraAddLogState.systemUpdateOffset = 0;
  renderCameraAddLog();

  try {
    await maintenancePost(
      '/api/system/maintenance/update-system'
    );
    flash('System update started','green');
  } catch(e) {
    cameraAddLogState.systemUpdate.push(
      `ERROR: ${e.message}`
    );
    renderCameraAddLog();
    flash(e.message,'red');
  }
}
async function validateInstallSolarTriggerRelease() {
  const input = document.getElementById('solartrigger-update-file');
  const button = document.getElementById('solartrigger-validate-install-release');
  const file = input && input.files[0];
  if (!file) {
    flash('Select a SolarTrigger ZIP package first.', 'red');
    return;
  }

  if (!confirm(
    `Validate, install and reboot with ${file.name}?\n\n` +
    'The package will be validated first. Installation will run only if validation succeeds. ' +
    'After a successful installation, the Raspberry Pi will reboot.'
  )) return;

  if (button) button.disabled = true;
  if (input) input.disabled = true;

  appendCameraAddLogLine(
    'solarTriggerUpdate',
    `Validating package: ${file.name}`
  );

  try {
    const form = new FormData();
    form.append('file', file);
    const validationResponse = await fetch(
      '/api/system/maintenance/upload-release',
      {method: 'POST', body: form}
    );
    const validation = await validationResponse.json();
    if (!validationResponse.ok) {
      throw new Error(validation.error || 'Invalid package');
    }

    const uploadToken = validation.upload_token;
    if (!uploadToken) {
      throw new Error('Validated package did not return an installation token');
    }

    document.getElementById('solartrigger-update-status').textContent =
      `Validated release: ${validation.version} · ${validation.file_count} files`;
    appendCameraAddLogLine(
      'solarTriggerUpdate',
      `Package validated: ${validation.version}`
    );
    appendCameraAddLogLine(
      'solarTriggerUpdate',
      'Installing validated SolarTrigger release…'
    );

    await maintenancePost(
      '/api/system/maintenance/install-release',
      {upload_token: uploadToken}
    );
    flash('Release installation accepted — Pi reboot requested', 'green');
  } catch (error) {
    appendCameraAddLogLine(
      'solarTriggerUpdate',
      `ERROR: ${error.message}`
    );
    flash(error.message, 'red');
    if (button) button.disabled = false;
    if (input) input.disabled = false;
  }
}

async function rollbackSolarTriggerRelease() {
  const select = document.getElementById('solartrigger-rollback-version');
  const version = select && select.value;
  if (!version) {
    flash('Select an installed rollback version.', 'red');
    return;
  }

  if (!confirm(
    `Rollback SolarTrigger to ${version}?\n\n` +
    'Only the active symlink will change; persistent data are shared. The Raspberry Pi will reboot.'
  )) return;

  appendCameraAddLogLine(
    'solarTriggerUpdate',
    `Rolling back to ${version}…`
  );
  try {
    await maintenancePost(
      '/api/system/maintenance/rollback-release',
      {version}
    );
    flash(`Rollback to ${version} accepted — Pi reboot requested`, 'green');
  } catch (error) {
    appendCameraAddLogLine(
      'solarTriggerUpdate',
      `ERROR: ${error.message}`
    );
    flash(error.message, 'red');
  }
}
setInterval(loadMaintenanceStatus,2000);setTimeout(loadMaintenanceStatus,250);
