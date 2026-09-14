// Smartphone GPS source extension.
// Loaded after solartrigger.js so it can reuse the existing Devices and GPS UI.
(() => {
  const SMARTPHONE_GPS_SOURCE = 'smartphone';
  const CLOCK_PROBE_COUNT = 12;
  const CLOCK_LOW_RTT_SAMPLE_COUNT = 4;
  const GPS_SOURCE_LABELS = {
    none: 'None',
    serial_nmea: 'GPS dongle — serial NMEA',
    gpsd: 'GPS dongle — gpsd',
    smartphone: 'Smartphone — geolocation + UTC',
  };

  if (!DEVICE_PLUGIN_OPTIONS.gps.includes(SMARTPHONE_GPS_SOURCE)) {
    DEVICE_PLUGIN_OPTIONS.gps.push(SMARTPHONE_GPS_SOURCE);
  }

  const baseDeviceOptions = deviceOptions;
  const baseRenderDevices = renderDevices;
  const baseUpdateGPS = updateGPS;
  const baseSyncGpsTimeLocation = syncGpsTimeLocation;

  function selectedGpsSource() {
    return state.gpsSource || 'none';
  }

  function gpsButtonLabel(source = selectedGpsSource()) {
    return source === SMARTPHONE_GPS_SOURCE
      ? 'SYNC TIME & LOCATION — SMARTPHONE'
      : 'SYNC TIME & LOCATION — GPS DONGLE';
  }

  function updateGpsSourceUi(source = selectedGpsSource()) {
    state.gpsSource = source || 'none';

    const button = document.getElementById('btn-gps-sync-time-location');
    GPS_ACTION_BUTTONS['btn-gps-sync-time-location'] = gpsButtonLabel(source);
    if (button && !button.disabled) button.textContent = gpsButtonLabel(source);

    const card = button && button.closest('.card');
    const title = card && card.querySelector('.card-title');
    if (title) {
      title.textContent = source === SMARTPHONE_GPS_SOURCE
        ? 'GPS (smartphone)'
        : 'GPS (dongle)';
    }

    const sourceLabel = document.querySelector(
      '[data-device-category="gps"] label[for="device-gps-select"]'
    );
    if (sourceLabel) sourceLabel.textContent = 'Source';

    const deviceStatus = document.querySelector(
      '[data-device-category="gps"] .device-status'
    );
    if (deviceStatus && source === SMARTPHONE_GPS_SOURCE) {
      deviceStatus.textContent = 'Selected source: smartphone browser geolocation + UTC';
    }
  }

  deviceOptions = function(category, device) {
    if (category !== 'gps') return baseDeviceOptions(category, device);

    const values = ['none', ...DEVICE_PLUGIN_OPTIONS.gps];
    [device.plugin, device.suggested_plugin].forEach(value => {
      if (value && !values.includes(value)) values.push(value);
    });

    return values.map(value => {
      const escapedValue = escapeDeviceText(value);
      const label = GPS_SOURCE_LABELS[value] || value;
      return `<option value="${escapedValue}">${escapeDeviceText(label)}</option>`;
    }).join('');
  };

  renderDevices = function(devices) {
    const source = devices && devices.gps && devices.gps.plugin
      ? devices.gps.plugin
      : 'none';

    // A smartphone is not USB-detected.  Once explicitly selected, expose it
    // as an available logical source; actual browser permission/capability is
    // checked only when the operator starts synchronization.
    const renderedDevices = source === SMARTPHONE_GPS_SOURCE
      ? {
          ...devices,
          gps: {
            ...(devices.gps || {}),
            detected: true,
          },
        }
      : devices;

    state.gpsSource = source;
    baseRenderDevices(renderedDevices);
    updateGpsSourceUi(source);
  };

  updateGPS = function(gps) {
    baseUpdateGPS(gps);

    if (selectedGpsSource() !== SMARTPHONE_GPS_SOURCE) return;

    const status = document.getElementById('gps-status-text');
    const icon = document.getElementById('gps-icon');
    const dotGps = document.getElementById('dot-gps');

    if (gps && gps.synced && gps.source === SMARTPHONE_GPS_SOURCE) {
      const accuracy = Number(gps.accuracy_m);
      const accuracyText = Number.isFinite(accuracy)
        ? ` — ±${accuracy.toFixed(0)} m`
        : '';
      const rtt = Number(gps.clock_best_rtt_ms);
      const rttText = Number.isFinite(rtt)
        ? ` — RTT ${rtt.toFixed(1)} ms`
        : '';
      if (status) status.textContent = `Smartphone synchronized ✓${accuracyText}${rttText}`;
      if (icon) icon.textContent = '🟢';
      if (dotGps) dotGps.className = 'dot on';
    } else {
      if (status) status.textContent = 'Smartphone selected — not synchronized';
      if (icon) icon.textContent = '🟡';
      if (dotGps) dotGps.className = 'dot warn';
    }
  };

  function smartphoneGeolocationError(error) {
    if (!error) return 'Smartphone location unavailable';
    if (error.code === 1) return 'Smartphone location permission denied';
    if (error.code === 2) return 'Smartphone location unavailable';
    if (error.code === 3) return 'Smartphone location request timed out';
    return error.message || 'Smartphone location unavailable';
  }

  function getSmartphonePosition() {
    return new Promise((resolve, reject) => {
      if (!window.isSecureContext) {
        reject(new Error(
          'Smartphone GPS requires the SolarTrigger HTTPS portal. Open https://solareclipse.local (or the configured SolarTrigger hostname) after trusting the SolarTrigger local CA.'
        ));
        return;
      }

      if (!navigator.geolocation) {
        reject(new Error(
          'Smartphone geolocation is unavailable in this browser. HTTPS or localhost may be required.'
        ));
        return;
      }

      navigator.geolocation.getCurrentPosition(
        resolve,
        error => reject(new Error(smartphoneGeolocationError(error))),
        {
          enableHighAccuracy: true,
          maximumAge: 0,
          timeout: 20000,
        },
      );
    });
  }

  function median(values) {
    if (!values.length) throw new Error('No smartphone clock samples');
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  async function probeSmartphoneClock() {
    // Date.now() supplies the absolute Unix epoch (UTC). performance.now()
    // supplies the interval clock, avoiding wall-clock jumps during the probe.
    const wallAnchorMs = Date.now();
    const monoAnchorMs = performance.now();
    const epochNowMs = () => wallAnchorMs + (performance.now() - monoAnchorMs);
    const samples = [];

    for (let index = 0; index < CLOCK_PROBE_COUNT; index += 1) {
      const t1 = epochNowMs();
      const p1 = performance.now();
      const response = await fetch('/api/gps/smartphone/time_probe', {
        method: 'POST',
        cache: 'no-store',
      });
      const p4 = performance.now();
      const t4 = epochNowMs();
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || `Clock probe failed (HTTP ${response.status})`);
      }

      const t2 = Number(payload.t2_epoch_ms);
      const t3 = Number(payload.t3_epoch_ms);
      if (!Number.isFinite(t2) || !Number.isFinite(t3) || t3 < t2) {
        throw new Error('Invalid Pi timestamp returned by clock probe');
      }

      // NTP four-timestamp formula.  theta is Pi - phone.  SolarTrigger stores
      // the correction to apply to the Pi, therefore phoneMinusPi = -theta.
      const piMinusPhoneMs = 0.5 * ((t2 - t1) + (t3 - t4));
      const phoneMinusPiMs = -piMinusPhoneMs;
      const serverProcessingMs = t3 - t2;
      const networkRttMs = Math.max(0, (p4 - p1) - serverProcessingMs);
      samples.push({phoneMinusPiMs, rttMs: networkRttMs});
    }

    const byRtt = [...samples].sort((a, b) => a.rttMs - b.rttMs);
    const selected = byRtt.slice(
      0,
      Math.min(CLOCK_LOW_RTT_SAMPLE_COUNT, byRtt.length),
    );
    return {
      offsetMs: median(selected.map(sample => sample.phoneMinusPiMs)),
      bestRttMs: byRtt[0].rttMs,
      selectedRttMs: median(selected.map(sample => sample.rttMs)),
      probeCount: samples.length,
    };
  }

  syncGpsTimeLocation = async function() {
    if (selectedGpsSource() !== SMARTPHONE_GPS_SOURCE) {
      return baseSyncGpsTimeLocation();
    }

    const button = document.getElementById('btn-gps-sync-time-location');
    if (button) {
      button.disabled = true;
      button.textContent = '⏳ LOCATING SMARTPHONE…';
    }

    try {
      const position = await getSmartphonePosition();
      if (button) button.textContent = '⏳ MEASURING UTC OFFSET…';
      const clock = await probeSmartphoneClock();
      const coords = position.coords;
      const clientEpochMs = Date.now();

      return runGpsAction(
        'btn-gps-sync-time-location',
        fetch('/api/gps/sync_time_location', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source: SMARTPHONE_GPS_SOURCE,
            client_epoch_ms: clientEpochMs,
            position_timestamp_ms: Number.isFinite(position.timestamp)
              ? position.timestamp
              : clientEpochMs,
            latitude: coords.latitude,
            longitude: coords.longitude,
            altitude_m: coords.altitude,
            accuracy_m: coords.accuracy,
            altitude_accuracy_m: coords.altitudeAccuracy,
            clock_offset_ms: clock.offsetMs,
            clock_best_rtt_ms: clock.bestRttMs,
            clock_selected_rtt_ms: clock.selectedRttMs,
            clock_probe_count: clock.probeCount,
          }),
        }),
        'Smartphone UTC time and location synchronization started…',
        true,
      );
    } catch (error) {
      flash(error.message || 'Smartphone synchronization failed', 'red');
      resetGpsActionButtons();
      return undefined;
    }
  };

  // Re-render once after installing the extension. The base script has already
  // rendered the provisional "none" source before this file is loaded.
  fetchDevices();
})();
