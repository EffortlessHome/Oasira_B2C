class ConfigPanel extends HTMLElement {

  set hass(hass) {
    this._hass = hass;
    if (this.parentNode) {
      this.populateCurrentUser();
      this.fetchNotificationDevices();
    }
  }
  async fetchNotificationDevices() {
    if (!this._hass || !this._hass.user || !this._hass.user.email) return;
    try {
      const result = await this._hass.callWS({
        type: "call_service",
        domain: "oasira_b2c",
        service: "get_notification_devices_for_person",
        service_data: { email: this._hass.user.email },
      });
      this._notificationDevices = (result && result.devices) || [];
    } catch (err) {
      this._notificationDevices = [];
      // Optionally log error
      // console.error("Failed to fetch notification devices", err);
    }
    this.renderNotificationDevices();
  }

  renderNotificationDevices() {
    const container = this.querySelector("#notification-devices-list");
    if (!container) return;
    if (!this._notificationDevices || this._notificationDevices.length === 0) {
      container.innerHTML = `<em>No notification devices registered.</em>`;
      return;
    }
    container.innerHTML = `
      <ul style="list-style:none;padding:0;">
        ${this._notificationDevices.map(dev => `
          <li style="margin-bottom:8px;">
            <strong>${dev.name}</strong> (${dev.platform})<br>
            <span style="font-size:0.85em;color:var(--secondary-text-color);">State: ${dev.state}</span>
          </li>
        `).join("")}
      </ul>
    `;
  }

  get hass() {
    return this._hass;
  }


  async connectedCallback() {
    this.innerHTML = `
      <style>
        ...existing code...
      </style>

      <div class="dashboard-container">
        <div class="header-section">
          <div class="profile-info">
            <div id="current-user">
              <img src="/local/oasira_b2c/user.png" alt="Profile">
              <h2 id="user-name">Loading...</h2>
              <p id="ha-url">Connecting...</p>
            </div>
            <div class="controls">
              <button id="logout-btn" class="btn btn-outline">Logout</button>
              <button id="restart-btn" class="btn btn-primary">Restart</button>
            </div>
            <div style="margin-top:24px;text-align:left;">
              <h3>Notification Devices</h3>
              <div id="notification-devices-list"><em>Loading...</em></div>
            </div>
          </div>

          <div class="system-status">
            ...existing code...
          </div>
        </div>

        <div class="nav-grid">
          ...existing code...
        </div>

        <div id="matter-section" class="matter-section">
          ...existing code...
        </div>

        <div class="footer-links">
          ...existing code...
        </div>
      </div>
    `;

    this.querySelector("#logout-btn")?.addEventListener("click", () => this.handleLogout());
    this.querySelector("#restart-btn")?.addEventListener("click", () => this.handleRestart());

    this.populateCurrentUser();
    this.fetchNotificationDevices();
    this.fetchMatterBridges();
  }

    this.querySelector("#logout-btn")?.addEventListener("click", () => this.handleLogout());
this.querySelector("#restart-btn")?.addEventListener("click", () => this.handleRestart());

this.populateCurrentUser();
this.fetchMatterBridges();
  }

  async handleLogout() {
  if (!this.hass) return;
  try {
    await this.hass.auth.revoke();
    if (window.localStorage) window.localStorage.clear();
    document.location.href = "/";
  } catch (err) {
    console.error(err);
    alert("Logout failed");
  }
}

  async handleRestart() {
  if (!this.hass) return;
  if (!confirm("Are you sure you want to restart Home Assistant?")) return;
  try {
    await this.hass.callService("homeassistant", "restart");
    alert("Restarting System...");
  } catch (err) {
    console.error(err);
    alert("Restart failed.");
  }
}

populateCurrentUser() {
  if (!this.hass) return;
  const nameEl = this.querySelector("#user-name");
  const urlEl = this.querySelector("#ha-url");
  if (nameEl) nameEl.textContent = this.hass.user.name;
  if (urlEl) urlEl.textContent = this.hass.states["sensor.ha_url"]?.state || "Connected";

  if (!this.hass.user.is_admin) {
    const restartBtn = this.querySelector("#restart-btn");
    if (restartBtn) restartBtn.style.display = "none";
  }
}

_tile(href, icon, label) {
  return `
      <a href="${href}" class="tile">
        <ha-icon icon="${icon}"></ha-icon>
        ${label}
      </a>
    `;
}

  async fetchMatterBridges() {
  const hostname = window.location.hostname;
  const url = `http://${hostname}:8482/api/matter/bridges`;
  const list = this.querySelector("#bridge-list");

  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error("Matter Hub unreachable");
    const bridges = await response.json();

    if (bridges && bridges.length > 0) {
      if (list) list.innerHTML = bridges.map(b => this._renderBridgeCard(b)).join("");
    } else {
      if (list) list.innerHTML = "<p>No Matter bridges found.</p>";
    }
  } catch (err) {
    console.error("Failed to fetch Matter bridges:", err);
    if (list) list.innerHTML = "<p style='color: var(--error-color, #f44336);'>Matter Hub unreachable or API failed.</p>";
  }
}

_renderBridgeCard(bridge) {
  const comm = bridge.commissioning || {};
  const info = bridge.basicInformation || {};

  // Generate a unique id for the factory reset button
  const resetBtnId = `factory-reset-btn-${bridge.id}`;

  // Attach the event listener after rendering
  setTimeout(() => {
    const btn = this.querySelector(`#${resetBtnId}`);
    if (btn) {
      btn.onclick = async () => {
        if (!confirm("Factory reset this bridge? This cannot be undone.")) return;
        btn.disabled = true;
        btn.textContent = "Resetting...";
        try {
          const hostname = window.location.hostname;
          const url = `http://${hostname}:8482/api/matter/bridges/${bridge.id}/actions/factory-reset`;
          const resp = await fetch(url, { method: "GET" });
          if (!resp.ok) throw new Error("Factory reset failed");
          alert("Bridge factory reset successfully.");
        } catch (err) {
          alert("Factory reset failed: " + (err.message || err));
        } finally {
          btn.disabled = false;
          btn.textContent = "Factory Reset";
        }
      };
    }
  }, 0);

  return `
      <div class="bridge-card">
        <div class="bridge-header">
          <h3>${bridge.name || "Matter Bridge"}</h3>
          <span class="status-badge status-${bridge.status === "running" ? "running" : "stopped"}">
            ${bridge.status}
          </span>
        </div>
        
        <div class="pairing-info">
          <span>Manual Pairing Code</span>
          <div class="pairing-code">${comm.manualPairingCode || "N/A"}</div>
          
          <div style="margin-top: 8px;">
            <span>Passcode: <strong>${comm.passcode || "N/A"}</strong></span>
            <span style="margin-left: 12px;">Discriminator: <strong>${comm.discriminator || "N/A"}</strong></span>
          </div>
        </div>

        <div style="font-size: 0.85rem; color: var(--secondary-text-color);">
           Devices: <strong>${bridge.deviceCount || 0}</strong><br>
           Vendor: ${info.vendorName || "Unknown"} | Version: ${info.softwareVersion || "N/A"}
        </div>

        <button id="${resetBtnId}" class="btn btn-outline" style="margin-top:10px;align-self:flex-start;">Factory Reset</button>
      </div>
    `;
}
}

customElements.define("Oasira-config-panel", ConfigPanel);
