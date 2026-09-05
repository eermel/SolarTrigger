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
let rigPhotoState = {rigs: []};
let globalDevicesState = null;

function renderRigPhotoConfig(payload) {
  const rigs = Array.isArray(payload && payload.rigs) ? payload.rigs : [];
  rigPhotoState = {rigs};

  rigs.forEach(rig => {
    const rigId = Number(rig.rig_id);
    if (!Number.isInteger(rigId) || rigId < 1 || rigId > 4) return;

    const photo = rig.photo || {};

    const antiBlur = document.getElementById(`rig-${rigId}-antiblur-switch`);
    const tolerance = document.getElementById(`rig-${rigId}-pixel-tolerance`);
    const isoComp = document.getElementById(`rig-${rigId}-iso-comp-switch`);
    const isoMax = document.getElementById(`rig-${rigId}-iso-max`);

    if (antiBlur) antiBlur.checked = photo.anti_trailing_enabled === true;

    if (tolerance) {
      tolerance.value = photo.motion_tolerance_px == null
        ? '1.0'
        : String(photo.motion_tolerance_px);
    }
    if (isoComp) {
      isoComp.checked = photo.iso_compensation_enabled !== false;
    }
    if (isoMax) {
      isoMax.value = String(photo.iso_max == null ? 6400 : photo.iso_max);
    }
  });

  const firstRig = rigs.find(rig => Number(rig.rig_id) === 1);
  const atmo = document.getElementById('cfg-atmo-switch');
  if (atmo && firstRig) {
    atmo.checked = Boolean(firstRig.photo && firstRig.photo.atmos_enabled === true);
  }
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
  const isoComp = document.getElementById(`rig-${rigId}-iso-comp-switch`);
  const isoMax = document.getElementById(`rig-${rigId}-iso-max`);
  const atmo = document.getElementById('cfg-atmo-switch');

  const toleranceValue = Number(tolerance && tolerance.value);
  const isoMaxValue = Number(isoMax && isoMax.value);

  if (!Number.isFinite(toleranceValue) || toleranceValue <= 0) {
    throw new Error('Pixel tolerance must be strictly positive');
  }
  if (!Number.isInteger(isoMaxValue) || isoMaxValue <= 0) {
    throw new Error('Invalid ISO Max');
  }

  return {
    rig_id: rigId,
    photo: {
      anti_trailing_enabled: Boolean(antiBlur && antiBlur.checked),
      motion_tolerance_px: toleranceValue,
      iso_compensation_enabled: Boolean(isoComp && isoComp.checked),
      iso_max: isoMaxValue,
      atmos_enabled: Boolean(atmo && atmo.checked),
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

async function persistGlobalAtmos(enabled, showFeedback = true) {
  try {
    const patches = [1, 2, 3, 4].map(rigId => ({
      rig_id: rigId,
      photo: {atmos_enabled: Boolean(enabled)},
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
        `Atmospheric Attenuation : ${enabled ? 'ON' : 'OFF'} pour tous les RIG`,
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
  await persistGlobalAtmos(Boolean(control && control.checked));
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

  const rigs = [1, 2, 3, 4].map(rigId => {
    const current = readRigPhotoConfig(rigId);

    return {
      rig_id: rigId,
      photo: {
        anti_trailing_enabled: current.photo.anti_trailing_enabled,
        motion_tolerance_px: current.photo.motion_tolerance_px,
        iso_compensation_enabled: current.photo.iso_compensation_enabled,
        iso_max: current.photo.iso_max
      }
    };
  });

  return {
    schema_version: 1,
    config_type: 'exposure_optimization',
    atmospheric_attenuation_enabled: Boolean(atmo && atmo.checked),
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

    const patches = (data.rigs || []).map(rig => ({
      rig_id: Number(rig.rig_id),
      photo: {
        ...(rig.photo || {}),
        atmos_enabled: atmos
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

function renderTriggerRigSelection() {
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
  if (device.fallback_physical_path) return `fallback:${device.fallback_physical_path}`;
  return null;
}

function persistedRigBinding(device) {
  if (!device) return null;
  const runtimeFields = new Set(['present', 'pilotable', 'display_label', 'transport_locator', 'busnum', 'devnum']);
  return Object.fromEntries(Object.entries(device).filter(([key]) => !runtimeFields.has(key)));
}

function encodedRigBinding(device) {
  return device ? encodeURIComponent(JSON.stringify(persistedRigBinding(device))) : '';
}

function rigDeviceDisplayLabel(category, device) {
  if (!device) return 'Inconnu';

  let label =
    device.display_label
    || device.model
    || device.serial
    || device.backend
    || 'Inconnu';

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

        let label = rigDeviceDisplayLabel(category, choice);
        if (isCurrent && current.present === false) label += ' — expected / not detected';
        if (choice.pilotable === false && !isExternalAltAz) label += ' — not controllable';
        if (assignedRig && assignedRig !== rigId) label += ` — assigned to RIG ${assignedRig}`;

        const optionBinding = isCurrent ? current : choice;
        const disabled = (
          (choice.pilotable === false && !isExternalAltAz)
          || (assignedRig && assignedRig !== rigId)
        );
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

    const data = await response.json();

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
  const byId = new Map(updatedRigs.map(rig => [Number(rig.rig_id), rig]));
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
      cameraColumn.hidden = false;

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
  const currentDevices = globalDevicesState;
  const focuserActive = Boolean(currentDevices && currentDevices.focuser && currentDevices.focuser.active === true);
  const mountActive = Boolean(currentDevices && currentDevices.mount && currentDevices.mount.active === true);

  const rigControlsActive = rigDevicesState.rigs.some(rig => {
    if (!rigIsOperationallyActive(rig)) {
      return false;
    }

    const rigDevices = rig && rig.devices ? rig.devices : {};
    const mount = rigDevices.mount;
    const mountBackend = mount && mount.backend;
    const focuser = rigDevices.focuser;
    const focuserBackend = focuser && (focuser.backend || focuser.plugin);

    const pilotableMount = Boolean(
      mount && ![null, '', 'none', 'external'].includes(mountBackend)
    );
    const pilotableFocuser = Boolean(
      focuser && ![null, '', 'none'].includes(focuserBackend)
    );

    return pilotableMount || pilotableFocuser;
  });

  const controlsActive = focuserActive || mountActive || rigControlsActive;
  const controlsTab = document.getElementById('controls-tab');
  const controlsPanel = document.getElementById('controls-panel');

  const controlsWasSelected = controlsTab.classList.contains('active');
  controlsTab.hidden = !controlsActive;
  controlsPanel.hidden = !controlsActive;
  renderControlsRigSelection();

  if (controlsWasSelected && !controlsActive) showTab(0);
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

async function refreshRigDevices(silent = false) {
  const button = document.getElementById('devices-rescan');
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/rigs/devices/refresh', {method: 'POST'});
    const inventory = await response.json();
    if (!response.ok) throw new Error(inventory.error || `HTTP error ${response.status}`);
    await loadRigDevices(inventory);
    await fetchDevices();
    if (!silent) flash('Device inventory refreshed', 'green');
  } catch (error) {
    flash(`Detection: ${error.message}`, 'red');
  } finally {
    if (button) button.disabled = false;
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
  if (!buf) { console.warn('Son non disponible :', filename); return; }

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
    console.warn('Impossible de charger la liste des éclipses:', error);
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
    }
  } catch (e) {
    console.warn('Impossible de recaler l\'heure depuis le statut:', e);
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
    }
  } catch (e) {
    console.warn('Impossible de recaler l\'heure après connexion:', e);
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
    console.warn('Impossible de rafraîchir l\'état GPS:', e);
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
socket.on('trigger_phase', d => {
  const rigId = Number(d && d.rig_id);
  if (!Number.isInteger(rigId) || rigId < 1 || rigId > 4) return;

  const key = String(rigId);
  state.triggerRigs[key] = {
    ...(state.triggerRigs[key] || {}),
    phase: d.phase,
  };

  if (rigId === selectedTriggerRigId) {
    updateSelectedTriggerPhase();
  }
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
      console.log('[DST] timezone_override reçu:', d.timezone_override);
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
  appendLog(d.text, d.level, d.source, d.timestamp);
  if (d.source === 'calculator') appendCalcLog(d.text, d.level);
});

// Historique complet à la (re)connexion
socket.on('log_history', lines => {
  const sources = ['gps_sync', 'calculator', 'trigger'];

  sources.forEach(source => {
    let containerId = 'log-container';
    if (source === 'gps_sync') containerId = 'log-container-gps_sync';
    else if (source === 'calculator') containerId = 'log-container-calculator';
    else if (source === 'trigger') containerId = 'log-container-trigger';

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
    absoluteMotion = data.moving === true && (data.motion_command === 'go' || data.motion_command === 'home')
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
    trackingSwitch.disabled = triggerRunning || !capabilities || capabilities.toggle !== true;
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

  function stopSlewBestEffort() {
    if (!activeSlew) return;
    const stopUrl = activeSlew.stopUrl;
    activeSlew = null;
    fetch(stopUrl, {method: 'POST'}).catch(() => {});
  }

  function startSlew(event) {
    const startUrl = mountUrl('slew/start');
    const stopUrl = mountUrl('slew/stop');
    if (!startUrl || !stopUrl || homing || activeSlew) return;
    event.preventDefault();
    const button = event.currentTarget;
    activeSlew = {button, pointerId: event.pointerId, stopUrl};
    button.setPointerCapture(event.pointerId);
    fetch(startUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({direction: button.dataset.direction}),
    }).catch(() => stopSlewBestEffort());
  }

  slewButtons.forEach(button => {
    button.addEventListener('pointerdown', startSlew);
    button.addEventListener('pointerup', stopSlewBestEffort);
    button.addEventListener('pointercancel', stopSlewBestEffort);
    button.addEventListener('lostpointercapture', stopSlewBestEffort);
    button.addEventListener('dragstart', event => event.preventDefault());
  });
  window.addEventListener('blur', stopSlewBestEffort);
  window.addEventListener('pagehide', stopSlewBestEffort);

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

  trackingSwitch.addEventListener('change', () => {
    postMount(mountUrl(trackingSwitch.checked
      ? 'tracking/start'
      : 'tracking/stop'));
  });

  document.addEventListener('controlsrigchange', () => {
    stopSlewBestEffort();
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
  idle:         'IDLE',
  waiting:      'EN ATTENTE',
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

  const btnStart = document.getElementById('btn-start');
  const btnStop  = document.getElementById('btn-stop');
  const btnTot   = document.getElementById('btn-totality-only');
  if (btnStart) btnStart.disabled = (phase !== 'idle');
  if (btnStop)  btnStop.disabled  = false;
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
  console.log('Vitesses sélectionnées:', selected);
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

  // Badge dans Contacts & Countdowns (page-2 CFG PHOTO et page-4 TRIGGER) — type pos GPS
  const triggerTypeEl = document.getElementById('trigger-eclipse-type');
  if (triggerTypeEl && typeGps !== '--') {
    triggerTypeEl.textContent = `${lsty.icon} ${_displayEclipseType(typeGps)}`;
    triggerTypeEl.style.color = lsty.color;
    const lbl = document.getElementById('trig-eclipse-label');
    if (lbl) lbl.textContent = data.title || data._eclipse || '--';
    const lbl2 = document.getElementById('trig-eclipse-type2');
    if (lbl2) { lbl2.textContent = `${gsty.icon} ${_displayEclipseType(typeGlobal)}`; lbl2.style.color = gsty.color; }
    const lbl3 = document.getElementById('trig-eclipse-type-gps');
    if (lbl3) { lbl3.textContent = `${lsty.icon} ${_displayEclipseType(typeGps)}`; lbl3.style.color = lsty.color; }
  }

  // TMAX toujours recalculé — le TMAX Jubier (magnitude max) ≠ milieu totalité
  // Totale → C2 + (C3-C2)/2  |  Partielle → C1 + (C4-C1)/2
  const tmilieu = _calcTmax(data.C1 || data.c1, data.C2 || data.c2,
                             data.C3 || data.c3, data.C4 || data.c4)
               || data.TMAX || data.tmax || null;

  const contacts = [
    { key: 'TSTART',  utc: data.TSTART || data.tstart, local_json: null, label: 'TSTART', desc: 'Sequence start', style: 'color:var(--green)' },
    { key: 'C1',      utc: data.C1 || data.c1,          local_json: data.C1_local,  label: 'C1',  desc: '1er contact', style: '' },
    { key: 'C2',      utc: data.C2 || data.c2,          local_json: data.C2_local,  label: 'C2',  desc: 'Totality start', style: '' },
    { key: 'TMILIEU', utc: tmilieu,                      local_json: null,           label: 'MID', desc: 'Mid-totality', style: '' },
    { key: 'C3',      utc: data.C3 || data.c3,          local_json: data.C3_local,  label: 'C3',  desc: 'Totality end', style: '' },
    { key: 'C4',      utc: data.C4 || data.c4,          local_json: data.C4_local,  label: 'C4',  desc: '4e contact', style: '' },
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
  const _buildContactsHtml = () => {
    return contacts.map(c => {
      const isTmax  = c.key === 'TMILIEU';
      const isBound = c.key === 'TSTART' || c.key === 'TEND';
      const labelColor = isTmax ? 'var(--accent)' : isBound ? 'var(--green)' : 'var(--text-dim)';
      const rowBorder  = isTmax ? 'border-color:rgba(245,166,35,.3)'
                       : isBound ? 'border-color:rgba(61,220,132,.2)' : '';
      return `<div class="contact-row" id="${c.key}"
          style="display:flex;justify-content:space-between;align-items:center;${rowBorder}">
        <span style="font-family:var(--mono);font-size:11px;color:${labelColor};width:52px;flex-shrink:0">${c.label}</span>
        <span style="font-family:var(--mono);font-size:13px;color:var(--blue);flex:1;text-align:center">${c.utc || '--'}</span>
        <span style="font-family:var(--mono);font-size:11px;color:var(--accent);flex:1;text-align:center">${c.local || '--'}</span>
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
    C3:      data.C3 || data.c3,
    C4:      data.C4 || data.c4,
    TEND:    data.TEND  || data.tend,
  };

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

  if (isNaN(lat) || isNaN(lon)) { flash('Lat/Lon requis', 'red'); return; }

  const btn = document.getElementById('btn-calc');
  btn.disabled = true; btn.textContent = '⏳ Calcul en cours…';

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
  const sel = document.getElementById('trigger-config-select');
  if (!sel) return;

  const rig = selectedTriggerRig();
  if (!rig) {
    sel.innerHTML = '<option value="">— No active RIG —</option>';
    sel.disabled = true;
    return;
  }

  sel.disabled = false;

  try {
    const r = await fetch('/api/configs/execution_plan/list');
    const d = await r.json();

    if (!r.ok || d.error) {
      throw new Error(d.error || `HTTP error ${r.status}`);
    }

    sel.innerHTML = '<option value="">— Select an Execution Plan —</option>';

    (d.files || []).forEach(filename => {
      const opt = document.createElement('option');
      opt.value = filename;
      opt.textContent = filename;
      sel.appendChild(opt);
    });

    const active = d.active
      ? d.active[String(selectedTriggerRigId)]
      : '';

    if (
      active &&
      Array.from(sel.options).some(opt => opt.value === active)
    ) {
      sel.value = active;
    } else {
      sel.value = '';
    }
  } catch (e) {
    flash(`Execution Plan list: ${e.message}`, 'red');
  }
}

async function loadTriggerPlan(filename) {
  if (!filename) return;

  const rig = selectedTriggerRig();
  if (!rig) {
    flash('No active RIG selected', 'red');
    return;
  }

  try {
    const r = await fetch('/api/trigger/select_execution_plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rig_id: selectedTriggerRigId,
        filename
      })
    });

    const d = await r.json();

    if (!r.ok || d.error) {
      flash(d.error || `HTTP error ${r.status}`, 'red');
      await loadTriggerConfigList();
      return;
    }

    flash(
      `RIG ${selectedTriggerRigId} — Execution Plan loaded: ${filename}`,
      'green'
    );

    if (d.circumstances) {
      renderContacts(d.circumstances);
    }
  } catch (e) {
    flash('Network error', 'red');
  }
}

async function startTrigger() {
  const r = await fetch('/api/trigger/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rig_id: selectedTriggerRigId})
  });
  const d = await r.json();
  if (d.error) {
    flash(d.error, 'red');
    // Erreurs bloquantes : rediriger vers l'onglet concerné
    if (d.code === 'GPS_NOT_SYNCED' || d.code === 'GPS_SYNC_STALE') {
      setTimeout(() => showTab(1), 1500);  // → onglet SYNC GPS
    } else if (d.code === 'JSON_INVALID') {
      setTimeout(() => showTab(2), 1500);  // → onglet ÉCLIPSE
    }
  } else {
    flash('Trigger started ▶', 'green');
  }
}

async function startDryRun() {
  if (!confirm(
    '🧪 Start a DRY-RUN ×1?\n' +
    'The selected Execution Plan will run at its original UTC times,\n' +
    'using today\'s UTC date. Sounds are included.'
  )) return;

  const r = await fetch('/api/trigger/dryrun', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rig_id: selectedTriggerRigId})
  });

  const d = await r.json();

  if (d.error) {
    flash(d.message || d.error, 'red');

    if (
      d.code === 'GPS_NOT_SYNCED' ||
      d.code === 'GPS_SYNC_STALE'
    ) {
      setTimeout(() => showTab(1), 1500);
    }
  } else {
    flash(
      'Dry-run ×1 started — today UTC, original plan times',
      'blue'
    );
  }
}

async function stopTrigger() {
  const btn = document.getElementById('btn-stop');
  // Confirmation seulement si le trigger tourne
  const isRunning = btn.textContent.includes('STOP') && !document.getElementById('btn-start').disabled === false;
  if (!confirm('⚠️ Stop / force-stop the trigger?')) return;
  btn.textContent = '⏳ Stopping…';
  try {
    const r = await fetch('/api/trigger/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rig_id: selectedTriggerRigId})
    });
    const d = await r.json();
    if (d.status === 'not_running') {
      flash('Trigger not active', 'yellow');
    } else {
      flash('■ Trigger stopped', 'yellow');
    }
  } catch(e) {
    flash('Network error while stopping', 'red');
  }
  btn.textContent = '■ STOP';
}

async function startTotalityOnly() {
  if (!confirm(
    '🌑 Override with Totality Sequence?\n' +
    'The current PHOTO sequence will be replaced immediately.\n' +
    'Audio announcements will continue.\n' +
    'Press STOP to stop photos for this RIG. Audio continues.'
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
        '🌑 Totality photo override active — audio continues',
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

function testSound(file) { playSound(file); }

function toggleSounds() {
  state.soundsEnabled = !state.soundsEnabled;
  document.getElementById('toggle-sounds').classList.toggle('on', state.soundsEnabled);
}

function setVolume(v) {
  state.volume = parseFloat(v) / 100;
  const lbl = document.getElementById('volume-label');
  if (lbl) lbl.textContent = `${Math.round(v)}%`;
}

// Initialiser le switch et le slider à leur état par défaut au chargement
document.addEventListener('DOMContentLoaded', () => {
  const sw = document.getElementById('toggle-sounds');
  if (sw) sw.classList.toggle('on', state.soundsEnabled);
  const sl = document.getElementById('volume-slider');
  if (sl) { sl.value = Math.round(state.volume * 100); setVolume(sl.value); }
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
  if (p.interval_s != null || p.interval != null) {
    document.getElementById('cfg-partial-interval').value = p.interval_s ?? p.interval;
  }
  if (dr.duration_s != null || dr.duration != null) {
    document.getElementById('cfg-dr-duration').value = dr.duration_s ?? dr.duration;
  }
  if (dr.interval_s != null || dr.interval != null) {
    document.getElementById('cfg-dr-interval').value = dr.interval_s ?? dr.interval;
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
  return {
    schema_version: 2,
    kind: 'capture_execution',
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
      _exposureOptAddLine(
        'Added to exposure list:',
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
  else if (source === 'sequencer') containerId = 'log-container-sequencer';

  const containers = document.querySelectorAll(`#${containerId}`);
  if (containers.length === 0) return;

  const d = document.createElement('div');
  d.className = `log-line ${level}`;
  d.textContent = ts ? `[${ts}] ${text}` : text;

  containers.forEach(c => {
    c.appendChild(d.cloneNode(true));

    const maxLines =
      source === 'sequencer'
        ? 3000
        : 600;

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
  else if (source === 'sequencer') containerId = 'log-container-sequencer';

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
// SEQUENCER
// ════════════════════════════════════════════════════════════════

let sequencerCircumstances = null;
let sequencerPhotoConfig = null;
let sequencerExposureOptConfig = null;
let sequencerPlanDate = '';


function _sequencerParseClock(value) {
  if (typeof value !== 'string') return null;

  const match = value.trim().match(
    /^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$/
  );

  if (!match) return null;

  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = Number(match[3]);
  const fraction = match[4] ? Number(`0.${match[4]}`) : 0;

  if (
    hours < 0 || hours > 23 ||
    minutes < 0 || minutes > 59 ||
    seconds < 0 || seconds > 59
  ) {
    return null;
  }

  return hours * 3600 + minutes * 60 + seconds + fraction;
}


function _sequencerFormatClock(totalSeconds) {
  if (!Number.isFinite(totalSeconds)) return '--';

  let value = totalSeconds % 86400;
  if (value < 0) value += 86400;

  let hh = Math.floor(value / 3600);
  value -= hh * 3600;

  let mm = Math.floor(value / 60);
  value -= mm * 60;

  let seconds = Math.floor(value);

  let milliseconds = Math.round(
    (value - seconds) * 1000
  );

  if (milliseconds === 1000) {
    milliseconds = 0;
    seconds += 1;
  }

  if (seconds === 60) {
    seconds = 0;
    mm += 1;
  }

  if (mm === 60) {
    mm = 0;
    hh = (hh + 1) % 24;
  }

  return (
    String(hh).padStart(2, '0') + ':' +
    String(mm).padStart(2, '0') + ':' +
    String(seconds).padStart(2, '0')
  );
}


function _sequencerShiftClock(value, deltaSeconds) {
  const parsed = _sequencerParseClock(value);

  if (parsed == null || !Number.isFinite(deltaSeconds)) {
    return '--';
  }

  return _sequencerFormatClock(parsed + deltaSeconds);
}


function _sequencerDisplayClock(value) {
  if (value == null || value === '') return '--';

  const text = String(value).trim();

  const match = text.match(
    /^(\d{1,2}:\d{2}:\d{2})(?:\.\d+)?$/
  );

  if (!match) return text;

  const parts = match[1].split(':');

  return (
    String(Number(parts[0])).padStart(2, '0') + ':' +
    parts[1] + ':' +
    parts[2]
  );
}


function _sequencerSet(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value == null ? '--' : value;
}


function appendSequencerLog(text, level = 'info') {
  const now = new Date();

  const ts = [
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0'),
  ].join(':');

  appendLog(text, level, 'sequencer', ts);
}


async function _sequencerFillSelect(
  selectId,
  url,
  placeholder,
  objectItems = false
) {
  const select = document.getElementById(selectId);
  if (!select) return;

  const current = select.value;

  const response = await fetch(url);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || `HTTP error ${response.status}`);
  }

  select.innerHTML = `<option value="">${placeholder}</option>`;

  (data.files || []).forEach(item => {
    const filename = objectItems ? item.name : item;
    if (!filename || filename === 'todayeclipse.json') return;

    const option = document.createElement('option');
    option.value = filename;
    option.textContent = filename;

    if (filename === current) {
      option.selected = true;
    }

    select.appendChild(option);
  });
}


async function loadSequencerConfigLists() {
  try {
    await Promise.all([
      _sequencerFillSelect(
        'sequencer-config-select',
        '/api/configs/list_sequence',
        '— Sequence file —'
      ),

      _sequencerFillSelect(
        'sequencer-circumstances-select',
        '/api/configs/list_eclipse',
        '— Circumstances file —',
        true
      ),

      _sequencerFillSelect(
        'sequencer-photo-select',
        '/api/configs/list_photo',
        '— Photo Setup file —'
      ),

      _sequencerFillSelect(
        'sequencer-exposure-opt-select',
        '/api/configs/list_exposure_opt',
        '— Exposure Optimization file —'
      ),

    ]);

    await updateSequencerPlanFilenames();

  } catch (error) {
    appendSequencerLog(
      `Configuration lists: ${error.message}`,
      'error'
    );
  }
}


function _sequencerRigById(rigId) {
  return (rigDevicesState.rigs || []).find(
    rig => Number(rig.rig_id) === Number(rigId)
  ) || null;
}


function _sequencerRigIsActive(rigId) {
  const rig = _sequencerRigById(rigId);

  return (
    Number(rigId) === 1 ||
    Boolean(rig && rig.enabled === true)
  );
}


function _sequencerSlug(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, '_')
    .replace(/[^A-Za-z0-9_-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^[_-]+|[_-]+$/g, '')
    .slice(0, 80);
}


function _sequencerCameraBrand(rig) {
  const camera =
    rig &&
    rig.devices &&
    rig.devices.camera;

  if (!camera) return '';

  const raw = String(
    camera.manufacturer ||
    camera.vendor ||
    camera.brand ||
    ''
  ).trim().toUpperCase();

  if (!raw) return '';

  const rules = [
    ['SONY', 'SONY'],
    ['NIKON', 'NIKON'],
    ['CANON', 'CANON'],
    ['FUJIFILM', 'FUJIFILM'],
    ['FUJI', 'FUJIFILM'],
    ['PANASONIC', 'PANASONIC'],
    ['OM DIGITAL', 'OM_SYSTEM'],
    ['OM SYSTEM', 'OM_SYSTEM'],
    ['OLYMPUS', 'OLYMPUS'],
    ['PENTAX', 'PENTAX'],
    ['RICOH', 'RICOH'],
    ['LEICA', 'LEICA'],
    ['HASSELBLAD', 'HASSELBLAD'],
    ['SIGMA', 'SIGMA'],
  ];

  const found = rules.find(
    ([marker]) => raw.includes(marker)
  );

  return found
    ? found[1]
    : _sequencerSlug(raw).toUpperCase();
}


function _sequencerPlanName() {
  const input =
    document.getElementById(
      'sequencer-plan-name'
    );

  return _sequencerSlug(
    input ? input.value : ''
  );
}


function _sequencerPlanPrefix(rigId) {
  const rig =
    _sequencerRigById(rigId);

  const brand =
    _sequencerCameraBrand(rig);

  const name =
    _sequencerPlanName();

  if (
    !sequencerPlanDate ||
    !brand ||
    !name
  ) {
    return '';
  }

  return (
    `exec_plan_${sequencerPlanDate}_` +
    `RIG${rigId}_${brand}_${name}`
  );
}


function _sequencerRigCanCompile(rigId) {
  const rig =
    _sequencerRigById(rigId);

  if (
    !rig ||
    !_sequencerRigIsActive(rigId)
  ) {
    return false;
  }

  const camera =
    rig.devices &&
    rig.devices.camera;

  const backend =
    camera &&
    String(
      camera.backend || ''
    ).trim().toLowerCase();

  const manufacturer =
    camera && (
      camera.manufacturer ||
      camera.vendor ||
      camera.brand
    );

  const model =
    camera &&
    camera.model;

  const focal = Number(
    rig.optics &&
    rig.optics.focal_length_mm
  );

  return Boolean(
    camera &&
    backend &&
    !['none', 'external'].includes(
      backend
    ) &&
    manufacturer &&
    model &&
    Number.isFinite(focal) &&
    focal > 0
  );
}


function renderSequencerPlanFilenames() {
  for (
    let rigId = 1;
    rigId <= 4;
    rigId += 1
  ) {
    const field =
      document.getElementById(
        `sequencer-plan-prefix-${rigId}`
      );

    const runButton =
      document.getElementById(
        `btn-run-sequencer-rig-${rigId}`
      );

    const row =
      document.querySelector(
        `.sequencer-plan-rig-row[data-rig-id="${rigId}"]`
      );

    const prefix =
      _sequencerPlanPrefix(rigId);

    const active =
      _sequencerRigIsActive(rigId);

    const canCompile =
      Boolean(prefix) &&
      _sequencerRigCanCompile(rigId);

    if (field) {
      field.value = prefix;

      field.placeholder =
        !active
          ? 'RIG inactive'
          : (
              !_sequencerCameraBrand(
                _sequencerRigById(rigId)
              )
                ? 'Camera manufacturer not configured'
                : 'Select circumstances and enter a plan name'
            );
    }

    if (runButton) {
      runButton.disabled =
        !canCompile;
    }

    if (row) {
      row.style.opacity =
        active ? '1' : '.55';
    }
  }

  const allButton =
    document.getElementById(
      'btn-run-all-sequencers'
    );

  if (allButton) {
    const activeRigIds =
      [1, 2, 3, 4].filter(
        _sequencerRigIsActive
      );

    allButton.disabled = (
      !_sequencerPlanName() ||
      !sequencerPlanDate ||
      activeRigIds.length === 0 ||
      activeRigIds.some(
        rigId =>
          !_sequencerRigCanCompile(
            rigId
          )
      )
    );
  }
}


async function updateSequencerPlanFilenames() {
  const select =
    document.getElementById(
      'sequencer-circumstances-select'
    );

  const filename =
    select ? select.value : '';

  sequencerPlanDate = '';

  if (!filename) {
    renderSequencerPlanFilenames();
    return;
  }

  try {
    const response = await fetch(
      '/api/configs/load_circumstances/' +
      encodeURIComponent(filename)
    );

    const circumstances =
      await response.json();

    if (!response.ok) {
      throw new Error(
        circumstances.error ||
        `HTTP error ${response.status}`
      );
    }

    const rawDate =
      circumstances._date ||
      circumstances._date_utc ||
      circumstances.date ||
      circumstances.eclipse_date ||
      '';

    const match =
      String(rawDate).match(
        /^(\d{4})-(\d{2})-(\d{2})/
      );

    if (!match) {
      throw new Error(
        'Eclipse date missing or invalid in circumstances file'
      );
    }

    sequencerCircumstances =
      circumstances;

    sequencerPlanDate =
      match[1] +
      match[2] +
      match[3];

    renderSequencer();

  } catch (error) {
    appendSequencerLog(
      `Execution Plan filename: ${error.message}`,
      'error'
    );

    renderSequencerPlanFilenames();
  }
}


async function cleanExecutionPlansForRig(
  rigId
) {
  if (!confirm(
    `Delete generated Execution Plan files for RIG ${rigId} only?`
  )) {
    return;
  }

  const button =
    document.getElementById(
      `btn-clean-execution-plans-rig-${rigId}`
    );

  if (button) {
    button.disabled = true;
  }

  try {
    const response = await fetch(
      '/api/configs/execution_plan/clean',
      {
        method: 'POST',
        headers: {
          'Content-Type':
            'application/json'
        },
        body: JSON.stringify({
          rig_id: rigId
        }),
      }
    );

    const result =
      await response.json();

    if (
      !response.ok ||
      result.status !== 'ok'
    ) {
      throw new Error(
        result.error ||
        `HTTP error ${response.status}`
      );
    }

    appendSequencerLog(
      `RIG ${rigId}: ` +
      `${result.deleted || 0} ` +
      'Execution Plan file(s) deleted',
      'success'
    );

    flash(
      `RIG ${rigId}: ` +
      `${result.deleted || 0} ` +
      'plan file(s) deleted',
      'green'
    );

    await loadTriggerConfigList();

  } catch (error) {
    appendSequencerLog(
      `RIG ${rigId} CLEAN: ` +
      error.message,
      'error'
    );

    flash(
      `RIG ${rigId} CLEAN: ` +
      error.message,
      'red'
    );

  } finally {
    renderSequencerPlanFilenames();
  }
}


document.addEventListener(
  'change',
  event => {
    if (
      event.target &&
      event.target.classList &&
      event.target.classList.contains(
        'rig-switch'
      )
    ) {
      renderSequencerPlanFilenames();
    }
  }
);


function readSequenceConfig() {
  const circumstances =
    document.getElementById('sequencer-circumstances-select');

  const photo =
    document.getElementById('sequencer-photo-select');

  const exposure =
    document.getElementById('sequencer-exposure-opt-select');

  const marginInput =
    document.getElementById('sequencer-margin-min');

  const marginMin = Number(
    marginInput ? marginInput.value : 60
  );

  return {
    schema_version: 1,
    config_type: 'sequence',
    circumstances_file: circumstances ? circumstances.value : '',
    photo_setup_file: photo ? photo.value : '',
    exposure_opt_file: exposure ? exposure.value : '',
    sequence_margin_min:
      Number.isFinite(marginMin) && marginMin >= 0
        ? marginMin
        : 60,
  };
}


async function saveSequenceConfig() {
  const select =
    document.getElementById('sequencer-config-select');

  const current = select && select.value
    ? select.value.replace(/\.json$/, '')
    : 'sequence';

  const name = prompt('Sequence file name:', current);
  if (!name) return;

  const data = readSequenceConfig();

  if (
    !data.circumstances_file ||
    !data.photo_setup_file ||
    !data.exposure_opt_file
  ) {
    flash('Select all Sequencer input files first', 'red');
    return;
  }

  const save = overwrite => fetch('/api/configs/save_sequence', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      filename: name,
      data,
      overwrite,
    }),
  });

  try {
    let response = await save(false);
    let result = await response.json();

    if (response.status === 409) {
      if (!confirm(
        `${result.filename || name} already exists. Overwrite it?`
      )) {
        return;
      }

      response = await save(true);
      result = await response.json();
    }

    if (!response.ok || result.status !== 'ok') {
      throw new Error(
        result.error || `HTTP error ${response.status}`
      );
    }

    await loadSequencerConfigLists();

    if (select) {
      select.value = result.filename;
    }

    flash('Saved: ' + result.filename, 'green');

  } catch (error) {
    flash(`Sequencer Save: ${error.message}`, 'red');
  }
}


async function loadSequenceConfig(filename) {
  if (!filename) return;

  try {
    const response = await fetch(
      '/api/configs/load_sequence/' +
      encodeURIComponent(filename)
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.error || `HTTP error ${response.status}`
      );
    }

    const circumstances =
      document.getElementById('sequencer-circumstances-select');

    const photo =
      document.getElementById('sequencer-photo-select');

    const exposure =
      document.getElementById('sequencer-exposure-opt-select');

    if (circumstances) {
      circumstances.value = data.circumstances_file || '';
    }

    if (photo) {
      photo.value = data.photo_setup_file || '';
    }

    if (exposure) {
      exposure.value = data.exposure_opt_file || '';
    }

    const marginInput =
      document.getElementById('sequencer-margin-min');

    if (marginInput) {
      const margin = Number(data.sequence_margin_min);

      marginInput.value =
        Number.isFinite(margin) && margin >= 0
          ? margin
          : 60;
    }

    await updateSequencerPlanFilenames();
    renderSequencer();

    appendSequencerLog(
      `Sequence configuration loaded: ${filename}`,
      'success'
    );

  } catch (error) {
    appendSequencerLog(
      `Load sequence: ${error.message}`,
      'error'
    );
  }
}


async function cleanSequenceConfigs() {
  if (!confirm(
    'Delete ALL saved Sequence JSON files?\n\nThis cannot be undone.'
  )) {
    return;
  }

  try {
    const response = await fetch(
      '/api/configs/sequence/clean',
      {method:'POST'}
    );

    const result = await response.json();

    if (!response.ok) {
      throw new Error(
        result.error || `HTTP error ${response.status}`
      );
    }

    flash(
      `${result.deleted || 0} Sequence file(s) deleted`,
      'yellow'
    );

    await loadSequencerConfigLists();

  } catch (error) {
    flash(`Sequencer CLEAN: ${error.message}`, 'red');
  }
}


function renderSequencerRigSummary() {
  const container = document.getElementById('sequencer-rigs-summary');
  if (!container) return;

  const rigs = Array.isArray(rigDevicesState.rigs)
    ? rigDevicesState.rigs
    : [];

  const byId = new Map(
    rigs.map(rig => [Number(rig.rig_id), rig])
  );

  const html = [1, 2, 3, 4].map(rigId => {
    const rig = byId.get(rigId) || null;

    const active =
      rigId === 1 ||
      Boolean(rig && rig.enabled === true);

    const devices =
      rig && rig.devices && typeof rig.devices === 'object'
        ? rig.devices
        : {};

    const camera =
      devices.camera && typeof devices.camera === 'object'
        ? devices.camera
        : {};

    const cameraName =
      camera.display_label ||
      camera.name ||
      camera.label ||
      camera.model ||
      camera.device_id ||
      camera.backend ||
      '—';

    const focal =
      rig &&
      rig.optics &&
      rig.optics.focal_length_mm != null
        ? `${rig.optics.focal_length_mm} mm`
        : '—';

    const status = active
      ? (rigId === 1 ? 'REQUIRED' : 'ON')
      : 'OFF';

    const statusColor = active
      ? 'var(--green)'
      : 'var(--text-dim)';

    const opacity = active ? '1' : '.55';

    return `
      <div style="
        min-width:0;
        padding:10px 12px;
        border:1px solid ${active ? 'var(--green)' : 'var(--border)'};
        border-radius:8px;
        background:var(--bg3);
        opacity:${opacity};
      ">
        <div style="
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:8px;
          margin-bottom:8px;
        ">
          <strong style="
            font-family:var(--mono);
            font-size:11px;
            color:var(--text);
          ">
            RIG ${rigId}
          </strong>

          <span style="
            font-family:var(--mono);
            font-size:10px;
            color:${statusColor};
          ">
            ${status}
          </span>
        </div>

        <div style="
          display:grid;
          grid-template-columns:auto minmax(0,1fr);
          gap:4px 10px;
          font-family:var(--mono);
          font-size:10px;
        ">
          <span style="color:var(--text-dim)">Camera</span>
          <span style="
            color:var(--text);
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
          ">
            ${escapeDeviceText(cameraName)}
          </span>

          <span style="color:var(--text-dim)">Focal</span>
          <span style="color:var(--text)">
            ${escapeDeviceText(focal)}
          </span>

          </div>
      </div>
    `;
  });

  container.innerHTML = html.join('');
}


function renderSequencer() {
  const eclipse =
    sequencerCircumstances ||
    state.eclipse ||
    {};

  const phases =
    sequencerPhotoConfig &&
    sequencerPhotoConfig.phases
      ? sequencerPhotoConfig.phases
      : {};

  const partial = phases.partial || {};
  const diamond = phases.diamond_ring || {};

  const c1 = eclipse.C1 || null;
  const c2 = eclipse.C2 || null;
  const tmax = eclipse.TMAX || eclipse.tmax || null;
  const c3 = eclipse.C3 || null;
  const c4 = eclipse.C4 || null;

  const marginInput =
    document.getElementById('sequencer-margin-min');

  const marginMin = Number(
    marginInput ? marginInput.value : 60
  );

  const marginSeconds =
    Number.isFinite(marginMin) && marginMin >= 0
      ? marginMin * 60
      : 3600;

  const tstart = c1
    ? _sequencerShiftClock(c1, -marginSeconds)
    : '--';

  const tend = c4
    ? _sequencerShiftClock(c4, marginSeconds)
    : '--';

  let drDuration = Number(
    diamond.duration_s ?? diamond.duration
  );

  if (!Number.isFinite(drDuration) || drDuration <= 0) {
    const input = document.getElementById('cfg-dr-duration');
    drDuration = Number(input && input.value);
  }

  if (!Number.isFinite(drDuration) || drDuration <= 0) {
    drDuration = Number(
      eclipse.diamond_ring &&
      eclipse.diamond_ring.duration_s != null
        ? eclipse.diamond_ring.duration_s
        : eclipse.duree_diamond_ring
    );
  }

  const hasDrDuration =
    Number.isFinite(drDuration) &&
    drDuration > 0;

  const c2Dr = hasDrDuration
    ? _sequencerShiftClock(c2, -drDuration)
    : '--';

  const c3Dr = hasDrDuration
    ? _sequencerShiftClock(c3, drDuration)
    : '--';

  _sequencerSet('seq-tstart', _sequencerDisplayClock(tstart));
  _sequencerSet('seq-c1', _sequencerDisplayClock(c1));
  _sequencerSet('seq-c2-dr', _sequencerDisplayClock(c2Dr));
  _sequencerSet('seq-c2', _sequencerDisplayClock(c2));
  _sequencerSet('seq-tmax', _sequencerDisplayClock(tmax));
  _sequencerSet('seq-c3', _sequencerDisplayClock(c3));
  _sequencerSet('seq-c3-dr', _sequencerDisplayClock(c3Dr));
  _sequencerSet('seq-c4', _sequencerDisplayClock(c4));
  _sequencerSet('seq-tend', _sequencerDisplayClock(tend));

  _sequencerSet(
    'seq-phase-1a',
    tstart && c2Dr !== '--'
      ? `${_sequencerDisplayClock(tstart)} → ${_sequencerDisplayClock(c2Dr)}`
      : '--'
  );

  _sequencerSet(
    'seq-phase-1b',
    c2Dr !== '--' && c2
      ? `${_sequencerDisplayClock(c2Dr)} → ${_sequencerDisplayClock(c2)}`
      : '--'
  );

  _sequencerSet(
    'seq-phase-2',
    c2 && c3
      ? `${_sequencerDisplayClock(c2)} → ${_sequencerDisplayClock(c3)}`
      : '--'
  );

  _sequencerSet(
    'seq-phase-3a',
    c3 && c3Dr !== '--'
      ? `${_sequencerDisplayClock(c3)} → ${_sequencerDisplayClock(c3Dr)}`
      : '--'
  );

  _sequencerSet(
    'seq-phase-3b',
    c3Dr !== '--' && tend
      ? `${_sequencerDisplayClock(c3Dr)} → ${_sequencerDisplayClock(tend)}`
      : '--'
  );

  _sequencerSet(
    'seq-dr-duration',
    hasDrDuration ? `${drDuration} s` : '--'
  );

  const partialInterval = Number(
    partial.interval_s ??
    partial.interval ??
    (
      document.getElementById('cfg-partial-interval') || {}
    ).value
  );

  _sequencerSet(
    'seq-partial-interval',
    Number.isFinite(partialInterval) && partialInterval > 0
      ? `${partialInterval} s`
      : '--'
  );

  const drInterval = Number(
    diamond.interval_s ??
    diamond.interval ??
    (
      document.getElementById('cfg-dr-interval') || {}
    ).value
  );

  _sequencerSet(
    'seq-dr-interval',
    Number.isFinite(drInterval) && drInterval > 0
      ? `${drInterval} s`
      : '--'
  );

  renderSequencerRigSummary();
  renderSequencerPlanFilenames();
}


async function _sequencerLoadInputs(
  config
) {
  const [
    circumstancesResponse,
    photoResponse,
    exposureResponse,
  ] = await Promise.all([
    fetch(
      '/api/configs/load_circumstances/' +
      encodeURIComponent(
        config.circumstances_file
      )
    ),
    fetch(
      '/api/configs/load_photo/' +
      encodeURIComponent(
        config.photo_setup_file
      )
    ),
    fetch(
      '/api/configs/load_exposure_opt/' +
      encodeURIComponent(
        config.exposure_opt_file
      )
    ),
  ]);

  const [
    circumstances,
    photo,
    exposure,
  ] = await Promise.all([
    circumstancesResponse.json(),
    photoResponse.json(),
    exposureResponse.json(),
  ]);

  if (!circumstancesResponse.ok) {
    throw new Error(
      'Circumstances: ' +
      (
        circumstances.error ||
        `HTTP ${circumstancesResponse.status}`
      )
    );
  }

  if (!photoResponse.ok) {
    throw new Error(
      'Photo Setup: ' +
      (
        photo.error ||
        `HTTP ${photoResponse.status}`
      )
    );
  }

  if (!exposureResponse.ok) {
    throw new Error(
      'Exposure Optimization: ' +
      (
        exposure.error ||
        `HTTP ${exposureResponse.status}`
      )
    );
  }

  sequencerCircumstances =
    circumstances;

  sequencerPhotoConfig =
    photo;

  sequencerExposureOptConfig =
    exposure;

  const rawDate =
    circumstances._date ||
    circumstances._date_utc ||
    circumstances.date ||
    circumstances.eclipse_date ||
    '';

  const match =
    String(rawDate).match(
      /^(\d{4})-(\d{2})-(\d{2})/
    );

  if (!match) {
    throw new Error(
      'Eclipse date missing or invalid in circumstances file'
    );
  }

  sequencerPlanDate =
    match[1] +
    match[2] +
    match[3];

  renderSequencer();
}


function _sequencerValidateRunInputs(
  config
) {
  if (
    !config.circumstances_file ||
    !config.photo_setup_file ||
    !config.exposure_opt_file
  ) {
    throw new Error(
      'Select all Sequencer input files first'
    );
  }

  const planName =
    _sequencerPlanName();

  if (!planName) {
    throw new Error(
      'Enter an Execution Plan name'
    );
  }

  return planName;
}


async function _compileSequencerRig(
  rigId,
  config,
  planName,
  showCommandLines = true
) {
  if (!_sequencerRigCanCompile(rigId)) {
    throw new Error(
      `RIG ${rigId} is inactive or incomplete`
    );
  }

  appendSequencerLog(
    `RIG ${rigId}: compilation started`,
    'info'
  );

  const response = await fetch(
    '/api/sequencer/compile',
    {
      method: 'POST',
      headers: {
        'Content-Type':
          'application/json',
      },
      body: JSON.stringify({
        rig_id: rigId,
        plan_name: planName,
        circumstances_file:
          config.circumstances_file,
        photo_setup_file:
          config.photo_setup_file,
        exposure_opt_file:
          config.exposure_opt_file,
        sequence_margin_min:
          config.sequence_margin_min,
      }),
    }
  );

  const result =
    await response.json();

  if (
    !response.ok ||
    result.status !== 'ok'
  ) {
    throw new Error(
      result.error ||
      `HTTP error ${response.status}`
    );
  }

  const field =
    document.getElementById(
      `sequencer-plan-prefix-${rigId}`
    );

  if (
    field &&
    result.filename
  ) {
    field.value =
      result.filename.replace(
        /\.plan$/i,
        ''
      );
  }

  appendSequencerLog(
    `RIG ${rigId}: saved ` +
    `${result.filename} ` +
    `(${result.command_count || 0} commands)`,
    'success'
  );

  if (showCommandLines) {
    appendSequencerLog(
      `──── RIG ${rigId} EXECUTION PLAN ────`,
      'info'
    );

    (result.lines || []).forEach(
      line => {
        appendSequencerLog(
          line,
          'info'
        );
      }
    );
  }

  return result;
}


async function runSequencerRig(
  rigId
) {
  const config =
    readSequenceConfig();

  const button =
    document.getElementById(
      `btn-run-sequencer-rig-${rigId}`
    );

  if (button) {
    button.disabled = true;
  }

  try {
    const planName =
      _sequencerValidateRunInputs(
        config
      );

    await _sequencerLoadInputs(
      config
    );

    await _compileSequencerRig(
      rigId,
      config,
      planName,
      true
    );

    await loadTriggerConfigList();

    flash(
      `RIG ${rigId} Execution Plan generated`,
      'green'
    );

  } catch (error) {
    appendSequencerLog(
      `RIG ${rigId}: Sequencer failed: ` +
      error.message,
      'error'
    );

    flash(
      `RIG ${rigId}: ${error.message}`,
      'red'
    );

  } finally {
    renderSequencerPlanFilenames();
  }
}


async function runAllSequencers() {
  const config =
    readSequenceConfig();

  const button =
    document.getElementById(
      'btn-run-all-sequencers'
    );

  if (button) {
    button.disabled = true;
  }

  const successes = [];
  const failures = [];

  try {
    const planName =
      _sequencerValidateRunInputs(
        config
      );

    await _sequencerLoadInputs(
      config
    );

    const activeRigIds =
      [1, 2, 3, 4].filter(
        _sequencerRigIsActive
      );

    appendSequencerLog(
      'Run all: ' +
      activeRigIds
        .map(id => `RIG ${id}`)
        .join(', '),
      'info'
    );

    /*
     * Deliberately sequential.
     * A failure on one RIG does not prevent
     * compilation of the following RIGs.
     */
    for (const rigId of activeRigIds) {
      try {
        const result =
          await _compileSequencerRig(
            rigId,
            config,
            planName,
            false
          );

        successes.push(result);

      } catch (error) {
        failures.push({
          rigId,
          error,
        });

        appendSequencerLog(
          `RIG ${rigId}: ${error.message}`,
          'error'
        );
      }
    }

    await loadTriggerConfigList();

    appendSequencerLog(
      'Run all completed: ' +
      `${successes.length} success, ` +
      `${failures.length} failure(s)`,
      failures.length
        ? 'error'
        : 'success'
    );

    if (failures.length) {
      const failedIds =
        failures
          .map(item => item.rigId)
          .join(', ');

      flash(
        'Run all completed with failures: ' +
        `RIG ${failedIds}`,
        'yellow'
      );
    } else {
      flash(
        `${successes.length} ` +
        'Execution Plan(s) generated',
        'green'
      );
    }

  } catch (error) {
    appendSequencerLog(
      'Run all failed before compilation: ' +
      error.message,
      'error'
    );

    flash(
      `Sequencer: ${error.message}`,
      'red'
    );

  } finally {
    renderSequencerPlanFilenames();
  }
}


// ════════════════════════════════════════════════════════════════
// NAVIGATION
// ════════════════════════════════════════════════════════════════
function showTab(n) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===n));
  document.querySelectorAll('.page').forEach((p,i) => p.classList.toggle('active', i===n));
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

fetchDevices();
refreshRigDevices(true);
loadSupportedEclipses();
loadEclipseData();
loadCameraStatus();
