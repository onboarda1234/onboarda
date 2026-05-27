/**
 * RegMind Flow Dashboard — JS Application Controller
 * Handles dynamic JSON data loading, state mapping, DOM rendering, and user filters
 */

document.addEventListener('DOMContentLoaded', () => {
  const state = {
    workflows: [],
    codeMap: [],
    agents: [],
    latestImpact: {},
    activeFilter: 'all',
    searchQuery: ''
  };

  // DOM Elements
  const el = {
    commitSha: document.getElementById('commit-sha'),
    generatedAt: document.getElementById('generated-at'),
    overallStatus: document.getElementById('overall-status'),
    overallStatusText: document.getElementById('overall-status-text'),
    
    kpiWorkflowsTotal: document.getElementById('kpi-workflows-total'),
    kpiWorkflowsAffected: document.getElementById('kpi-workflows-affected'),
    kpiControlsAffected: document.getElementById('kpi-controls-affected'),
    kpiAgentsAffected: document.getElementById('kpi-agents-affected'),
    kpiValidationRequired: document.getElementById('kpi-validation-required'),

    workflowTimeline: document.getElementById('workflow-timeline'),
    agentsGrid: document.getElementById('agents-grid'),
    changedFilesList: document.getElementById('changed-files-list'),
    checklistContainer: document.getElementById('checklist-container'),
    explanationContainer: document.getElementById('explanation-container'),

    searchBar: document.getElementById('search-bar'),
    filterTabs: document.querySelectorAll('.btn-tab')
  };

  // Initialize
  async function init() {
    try {
      console.log('Loading RegMind Workflow Data models...');
      
      // Fetch datasets
      const [workflowsRes, codeMapRes, agentsRes, latestImpactRes] = await Promise.all([
        fetch('data/workflow-map.json'),
        fetch('data/code-map.json'),
        fetch('data/agents-map.json'),
        fetch('data/latest-impact.json')
      ]);

      state.workflows = await workflowsRes.json();
      state.codeMap = await codeMapRes.json();
      state.agents = await agentsRes.json();
      state.latestImpact = await latestImpactRes.json();

      console.log('Data loaded successfully. Propagating UI elements...');
      renderUI();
      setupEventListeners();
      initVisualizer();
    } catch (error) {
      console.error('Failed to initialize RegMind Flow Dashboard:', error);
      alert('Error loading dashboard files. Ensure you are running a local static file server (e.g. python -m http.server).');
    }
  }

  // Render Page
  function renderUI() {
    renderHeader();
    renderKPIs();
    renderWorkflowTimeline();
    renderAgentsGrid();
    renderChangedFiles();
    renderValidationChecklist();
    renderNonCoderExplanations();
  }

  // Render Header Metadata
  function renderHeader() {
    const impact = state.latestImpact;
    el.commitSha.textContent = impact.commit_sha ? impact.commit_sha.substring(0, 8) : 'Stable';
    el.generatedAt.textContent = impact.generated_at ? new Date(impact.generated_at).toLocaleString() : 'N/A';

    const risk = (impact.risk_level || 'STABLE').toUpperCase();
    
    // Clear existing statuses
    el.overallStatus.className = 'status-indicator';
    
    if (risk === 'STABLE') {
      el.overallStatus.classList.add('status-stable');
      el.overallStatusText.textContent = 'Stable';
    } else if (risk === 'LOW') {
      el.overallStatus.classList.add('status-changed');
      el.overallStatusText.textContent = 'Changed (Low Risk)';
    } else if (risk === 'MEDIUM') {
      el.overallStatus.classList.add('status-review');
      el.overallStatusText.textContent = 'Needs Review';
    } else {
      el.overallStatus.classList.add('status-critical');
      el.overallStatusText.textContent = 'Critical Changes';
    }
  }

  // Render Summary Cards
  function renderKPIs() {
    const impact = state.latestImpact;
    el.kpiWorkflowsTotal.textContent = state.workflows.length;
    el.kpiWorkflowsAffected.textContent = impact.affected_workflows ? impact.affected_workflows.length : 0;
    
    // Count critical controls
    let criticalControlsCount = 0;
    if (impact.changed_files) {
      const changedFilePaths = impact.changed_files.map(f => f.file_path);
      state.codeMap.forEach(item => {
        if (changedFilePaths.includes(item.file_path) && item.risk_level === 'critical') {
          criticalControlsCount += item.affected_controls ? item.affected_controls.length : 1;
        }
      });
    }
    el.kpiControlsAffected.textContent = criticalControlsCount;
    el.kpiAgentsAffected.textContent = impact.affected_agents ? impact.affected_agents.length : 0;
    el.kpiValidationRequired.textContent = impact.validation_required ? impact.validation_required.length : 0;
  }

  // Render Main Workflow Map (15 nodes)
  function renderWorkflowTimeline() {
    el.workflowTimeline.innerHTML = '';
    const impact = state.latestImpact;
    const affected = impact.affected_workflows || [];

    const filteredWorkflows = state.workflows.filter(wf => {
      // 1. Search Query filter
      if (state.searchQuery) {
        const query = state.searchQuery.toLowerCase();
        const matchesQuery = wf.label.toLowerCase().includes(query) || 
                             wf.description.toLowerCase().includes(query) ||
                             (wf.connected_files && wf.connected_files.some(f => f.toLowerCase().includes(query)));
        if (!matchesQuery) return false;
      }

      // 2. Tab filters
      if (state.activeFilter === 'affected') {
        return affected.includes(wf.id);
      } else if (state.activeFilter === 'critical') {
        return wf.compliance_importance === 'critical';
      } else if (state.activeFilter === 'backend') {
        return wf.connected_files && wf.connected_files.some(f => f.includes('.py'));
      } else if (state.activeFilter === 'frontend') {
        return wf.connected_files && wf.connected_files.some(f => f.includes('.html'));
      } else if (state.activeFilter === 'compliance') {
        return wf.compliance_importance === 'critical' || wf.compliance_importance === 'high';
      }
      return true;
    });

    if (filteredWorkflows.length === 0) {
      el.workflowTimeline.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding:2rem;">No matching workflow steps found.</div>';
      return;
    }

    filteredWorkflows.forEach(wf => {
      const isAffected = affected.includes(wf.id);
      
      let nodeClass = '';
      let badgeClass = 'status-stable';
      let badgeText = 'Stable';

      if (isAffected) {
        const globalRisk = (impact.risk_level || 'STABLE').toLowerCase();
        if (globalRisk === 'critical') {
          nodeClass = 'node-critical';
          badgeClass = 'status-critical';
          badgeText = 'Critical Impact';
        } else if (globalRisk === 'medium') {
          nodeClass = 'node-affected';
          badgeClass = 'status-review';
          badgeText = 'Needs Review';
        } else {
          nodeClass = 'node-changed';
          badgeClass = 'status-changed';
          badgeText = 'Changed';
        }
      }

      const nodeDiv = document.createElement('div');
      nodeDiv.className = `timeline-node ${nodeClass}`;
      nodeDiv.id = `wf-${wf.id}`;

      // Assemble list strings
      const filesStr = wf.connected_files ? wf.connected_files.map(f => `<li>${f}</li>`).join('') : '<li>None</li>';
      const routesStr = wf.connected_routes ? wf.connected_routes.map(r => `<li>${r}</li>`).join('') : '<li>None</li>';
      const tablesStr = wf.connected_tables ? wf.connected_tables.map(t => `<li>${t}</li>`).join('') : '<li>None</li>';
      const valStr = wf.validation_required ? wf.validation_required.map(v => `<li>${v}</li>`).join('') : '<li>None</li>';

      nodeDiv.innerHTML = `
        <div class="node-bullet"></div>
        <div class="node-header">
          <div class="node-title-area">
            <div class="node-number">${wf.stage_order}</div>
            <div class="node-title">${wf.label}</div>
          </div>
          <span class="node-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="node-body">
          <p class="node-desc">${wf.description}</p>
          <div class="node-meta-grid">
            <div class="meta-item">
              <span class="meta-label">Business Owner</span>
              <span class="meta-value-text">${wf.business_owner}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">User Type</span>
              <span class="meta-value-text" style="text-transform: capitalize;">${wf.user_type}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Compliance Importance</span>
              <span class="meta-value-text" style="text-transform: capitalize;">${wf.compliance_importance}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Connected Agents</span>
              <span class="meta-value-text">${wf.connected_agents && wf.connected_agents.length > 0 ? wf.connected_agents.join(', ') : 'None'}</span>
            </div>
          </div>
          <div class="node-expandable">
            <button class="btn-toggle-detail" data-target="detail-${wf.id}">
              <svg style="width:12px; height:12px; transform:rotate(90deg);" viewBox="0 0 24 24"><path d="M8.59,16.59L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.59Z"/></svg>
              Show Technical Mappings
            </button>
            <div class="node-details" id="detail-${wf.id}">
              <div class="details-block">
                <div class="details-block-title">Connected Source Files</div>
                <ul class="details-block-list">${filesStr}</ul>
              </div>
              <div class="details-block">
                <div class="details-block-title">Associated REST API Routes</div>
                <ul class="details-block-list">${routesStr}</ul>
              </div>
              <div class="details-block">
                <div class="details-block-title">Database Tables Affected</div>
                <ul class="details-block-list">${tablesStr}</ul>
              </div>
              <div class="details-block">
                <div class="details-block-title">Regulator Verification Strategy</div>
                <ul class="details-block-list" style="color:var(--text-primary); font-weight:500;">${valStr}</ul>
              </div>
            </div>
          </div>
        </div>
      `;

      el.workflowTimeline.appendChild(nodeDiv);
    });

    // Wire up dynamic collapsible detail toggles
    document.querySelectorAll('.btn-toggle-detail').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const targetId = btn.getAttribute('data-target');
        const detailsEl = document.getElementById(targetId);
        detailsEl.classList.toggle('active');
        if (detailsEl.classList.contains('active')) {
          btn.innerHTML = `
            <svg style="width:12px; height:12px; transform:rotate(-90deg);" viewBox="0 0 24 24"><path d="M8.59,16.59L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.59Z"/></svg>
            Hide Technical Mappings
          `;
        } else {
          btn.innerHTML = `
            <svg style="width:12px; height:12px; transform:rotate(90deg);" viewBox="0 0 24 24"><path d="M8.59,16.59L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.59Z"/></svg>
            Show Technical Mappings
          `;
        }
      });
    });
  }

  // Render AI Agents Map Grid
  function renderAgentsGrid() {
    el.agentsGrid.innerHTML = '';
    const impact = state.latestImpact;
    const affected = impact.affected_agents || [];

    const filteredAgents = state.agents.filter(agent => {
      if (state.searchQuery) {
        const query = state.searchQuery.toLowerCase();
        return agent.name.toLowerCase().includes(query) || agent.role.toLowerCase().includes(query);
      }
      return true;
    });

    if (filteredAgents.length === 0) {
      el.agentsGrid.innerHTML = '<div style="color:var(--text-muted); padding:2rem; text-align:center; grid-column:1/-1;">No matching AI agents found.</div>';
      return;
    }

    filteredAgents.forEach(agent => {
      const isAffected = affected.includes(agent.id);
      
      let agentClass = '';
      let badgeClass = 'status-stable';
      let badgeText = 'Stable';

      if (isAffected) {
        const globalRisk = (impact.risk_level || 'STABLE').toLowerCase();
        if (globalRisk === 'critical') {
          agentClass = 'agent-critical';
          badgeClass = 'status-critical';
          badgeText = 'Critical Impact';
        } else {
          agentClass = 'agent-affected';
          badgeClass = 'status-review';
          badgeText = 'Needs Review';
        }
      }

      const card = document.createElement('div');
      card.className = `agent-card ${agentClass}`;

      card.innerHTML = `
        <div class="agent-header">
          <div class="agent-title">${agent.name}</div>
          <span class="node-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="agent-meta">
          Authority: <span>${agent.authority_level}</span>
        </div>
        <div class="agent-desc">${agent.role}</div>
        <div class="node-meta-grid" style="font-size:0.75rem;">
          <div class="meta-item">
            <span class="meta-label">Connected Workflows</span>
            <span class="meta-value-text" style="color:var(--text-primary);">${agent.workflows.length > 0 ? agent.workflows.join(', ') : 'None'}</span>
          </div>
          <div class="meta-item" style="margin-top:0.5rem;">
            <span class="meta-label">Execution Input</span>
            <span class="meta-value-text">${agent.inputs}</span>
          </div>
          <div class="meta-item" style="margin-top:0.5rem;">
            <span class="meta-label">Risks & Gaps</span>
            <span class="meta-value-text" style="color:var(--color-review);">${agent.risks}</span>
          </div>
        </div>
      `;

      el.agentsGrid.appendChild(card);
    });
  }

  // Render Changed Files list
  function renderChangedFiles() {
    el.changedFilesList.innerHTML = '';
    const impact = state.latestImpact;
    const changed = impact.changed_files || [];

    if (changed.length === 0) {
      el.changedFilesList.innerHTML = `
        <div class="file-item" style="border-left: 4px solid var(--color-stable); text-align: center; padding: 1.5rem;">
          <div class="file-name" style="color: var(--color-stable); font-size: 1rem; margin-bottom: 0.25rem;">✓ Working Tree Stable</div>
          <span class="file-desc">No code changes detected in the current revision mapping. All core components are stable.</span>
        </div>
      `;
      return;
    }

    changed.forEach(file => {
      const risk = (file.risk_level || 'low').toLowerCase();
      let badgeClass = 'status-stable';
      if (risk === 'critical') badgeClass = 'status-critical';
      else if (risk === 'high') badgeClass = 'status-review';
      else if (risk === 'medium') badgeClass = 'status-changed';

      const fileItem = document.createElement('div');
      fileItem.className = 'file-item';

      fileItem.innerHTML = `
        <div class="file-item-header">
          <div class="file-name">${file.file_path}</div>
          <span class="node-badge ${badgeClass}" style="font-size:0.65rem; padding:0.15rem 0.5rem;">${risk.toUpperCase()}</span>
        </div>
        <div class="file-desc">${file.plain_english_impact}</div>
      `;

      el.changedFilesList.appendChild(fileItem);
    });
  }

  // Render Validation Checklist Panel
  function renderValidationChecklist() {
    el.checklistContainer.innerHTML = '';
    const impact = state.latestImpact;
    const validation = impact.validation_required || [];

    if (validation.length === 0) {
      el.checklistContainer.innerHTML = `
        <div class="checklist-item" style="border-left: 4px solid var(--color-stable);">
          <div class="checklist-icon" style="color: var(--color-stable);">
            <svg style="width:1.25rem; height:1.25rem;" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2m-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9Z"/></svg>
          </div>
          <div class="checklist-text" style="color: var(--text-secondary);">
            No validation required. The codebase has not diverged from GitHub main.
          </div>
        </div>
      `;
      return;
    }

    validation.forEach(item => {
      const checklistItem = document.createElement('div');
      checklistItem.className = 'checklist-item';
      checklistItem.innerHTML = `
        <div class="checklist-icon" style="color: var(--color-review);">
          <svg style="width:1.25rem; height:1.25rem;" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2m-1 5h2v6h-2V7m0 8h2v2h-2v-2Z"/></svg>
        </div>
        <div class="checklist-text">${item}</div>
      `;
      el.checklistContainer.appendChild(checklistItem);
    });
  }

  // Render Non-Coder Explanations Panel
  function renderNonCoderExplanations() {
    el.explanationContainer.innerHTML = '';
    const impact = state.latestImpact;
    const affected = impact.affected_workflows || [];

    if (affected.length === 0) {
      el.explanationContainer.innerHTML = `
        <div class="plain-box" style="border-color: hsla(142, 72%, 45%, 0.15); background: hsla(142, 72%, 45%, 0.05);">
          <p style="color: var(--text-secondary);">
            All business workflows are stable. No compliance risks, AI agent behaviors, or audit parameters have been affected in this commit.
          </p>
        </div>
      `;
      return;
    }

    const plainBox = document.createElement('div');
    plainBox.className = 'plain-box';

    let explanationHTML = `<p style="margin-bottom:1rem; font-weight:600; color:var(--text-primary);">A non-technical explanation of the current diff impact:</p>`;

    affected.forEach(wfId => {
      const wf = state.workflows.find(w => w.id === wfId);
      if (wf) {
        explanationHTML += `
          <div style="margin-bottom:1.25rem; padding-bottom:1.25rem; border-bottom:1px solid var(--border-color);">
            <div style="font-weight:600; color:var(--color-review); margin-bottom:0.25rem;">• Step ${wf.stage_order}: ${wf.label}</div>
            <p style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:0.25rem;">
              <strong>Business Impact:</strong> ${wf.plain_english_explanation}
            </p>
            <p style="font-size:0.85rem; color:var(--text-muted);">
              <strong>Auditor Safety Advice:</strong> Ensure that the compliance team executes the corresponding verification checklists before deploying these changes to the live staging environment.
            </p>
          </div>
        `;
      }
    });

    plainBox.innerHTML = explanationHTML;
    el.explanationContainer.appendChild(plainBox);
  }

  // Event Listeners for Filters
  function setupEventListeners() {
    // Search Bar
    el.searchBar.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      renderWorkflowTimeline();
      renderAgentsGrid();
    });

    // Filter Tabs
    el.filterTabs.forEach(tab => {
      tab.addEventListener('click', () => {
        el.filterTabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.activeFilter = tab.getAttribute('data-filter');
        renderWorkflowTimeline();
      });
    });
  }

  // ============================================================================
  // RegMind Live AI Workflow Simulator & Agent Visualizer Controller
  // ============================================================================

  // Visualizer dataset representing the 6 sequential operational stages
  const visualizerStages = {
    1: {
      title: "Client Intake & ID Verification",
      activeAgent: "Agent 1: Identity & Document Verification",
      activeNodes: [1, 3], // Client details, Document uploads
      activeFilaments: ["filament-1", "filament-3"],
      activeRightFilaments: ["filament-right-1"],
      impactLevel: "stable",
      sphereRole: "Verification Core",
      sphereLabel: "Identity OCR",
      themeClass: "theme-stable",
      iconBg: "rgba(16, 185, 129, 0.12)",
      iconColor: "hsl(142, 72%, 45%)",
      iconBorder: "1px solid rgba(16, 185, 129, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
        </svg>
      `,
      summaryText: "Agent 1 parsed uploaded document packages (Section A/B) using Claude Vision. Extracted key details, checked credentials authenticity, and verified document magic bytes successfully."
    },
    2: {
      title: "PEP & Watchlist Screening",
      activeAgent: "Agent 3 & 7: FinCrime screening",
      activeNodes: [1, 2, 5], // Client details, Creating alerts, Data policies
      activeFilaments: ["filament-1", "filament-2", "filament-5"],
      activeRightFilaments: ["filament-right-2"],
      impactLevel: "needs review",
      sphereRole: "Advisory Desk",
      sphereLabel: "Screening Engine",
      themeClass: "theme-review",
      iconBg: "rgba(245, 158, 11, 0.12)",
      iconColor: "hsl(38, 92%, 50%)",
      iconBorder: "1px solid rgba(245, 158, 11, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
        </svg>
      `,
      summaryText: "Agent 3 processed global PEP & sanctions screening hits. Isolated high-weight matching names on official watchlists. Handled false positives and routed genuine threats for human escalation."
    },
    3: {
      title: "KYB & Corporate Shareholder Maps",
      activeAgent: "Agent 4: Corporate structure analysis",
      activeNodes: [1, 3, 4], // Client details, Document uploads, Accessing data
      activeFilaments: ["filament-1", "filament-3", "filament-4"],
      activeRightFilaments: ["filament-right-1", "filament-right-2"],
      impactLevel: "stable",
      sphereRole: "Structure Core",
      sphereLabel: "UBO Mapper",
      themeClass: "theme-stable",
      iconBg: "rgba(16, 185, 129, 0.12)",
      iconColor: "hsl(142, 72%, 45%)",
      iconBorder: "1px solid rgba(16, 185, 129, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
        </svg>
      `,
      summaryText: "Agent 4 scanned and deconstructed complex corporate registry filings. Built incorporation ownership graphs and mapped ultimate beneficial owners (UBOs) above statutory thresholds."
    },
    4: {
      title: "AI Compliance Memo Generation",
      activeAgent: "Agent 5: Risk & Memo Composer",
      activeNodes: [3, 4, 5], // Document uploads, Accessing data, Data policies
      activeFilaments: ["filament-3", "filament-4", "filament-5"],
      activeRightFilaments: ["filament-right-3"],
      impactLevel: "changed",
      sphereRole: "Drafting Desk",
      sphereLabel: "Memo Writer",
      themeClass: "theme-changed",
      iconBg: "rgba(59, 130, 246, 0.12)",
      iconColor: "hsl(217, 91%, 60%)",
      iconBorder: "1px solid rgba(59, 130, 246, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
        </svg>
      `,
      summaryText: "Agent 5 compiled document logs, registry files, and risk parameters to compose a formal 11-section Compliance Memo. Optimized hours of manual research into a cohesive audit summary."
    },
    5: {
      title: "Lead AI Supervisor Review Gate",
      activeAgent: "Agent 10: Ongoing compliance reviewer",
      activeNodes: [1, 2, 3, 4, 5], // All active
      activeFilaments: ["filament-1", "filament-2", "filament-3", "filament-4", "filament-5"],
      activeRightFilaments: ["filament-right-1", "filament-right-2", "filament-right-3"],
      impactLevel: "stable",
      sphereRole: "Audit Supervisor",
      sphereLabel: "Review Gate",
      themeClass: "theme-stable",
      iconBg: "rgba(16, 185, 129, 0.12)",
      iconColor: "hsl(142, 72%, 45%)",
      iconBorder: "1px solid rgba(16, 185, 129, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
        </svg>
      `,
      summaryText: "Agent 10 evaluated system outputs against raw database details. Flagged zero logical contradictions, performed deterministic compliance tests, and established secure log seals."
    },
    6: {
      title: "Monitoring alert",
      activeAgent: "Agent 6 & 8: Ongoing monitoring",
      activeNodes: [1, 2, 4], // Client details, Creating alerts, Accessing data
      activeFilaments: ["filament-1", "filament-2", "filament-4"],
      activeRightFilaments: ["filament-right-1", "filament-right-2"],
      impactLevel: "needs review",
      sphereRole: "Drift Monitor",
      sphereLabel: "Ongoing Engine",
      themeClass: "theme-review",
      iconBg: "rgba(217, 32, 90, 0.12)",
      iconColor: "hsl(350, 89%, 60%)",
      iconBorder: "1px solid rgba(217, 32, 90, 0.25)",
      iconSvg: `
        <svg style="width:18px; height:18px; fill:currentColor;" viewBox="0 0 24 24">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
        </svg>
      `,
      summaryText: "Ownership structure changed after onboarding, creating a mismatch with the previously verified UBO. Review required."
    }
  };

  function initVisualizer() {
    const tabs = document.querySelectorAll('.visualizer-tab-btn');
    const leftNodes = document.querySelectorAll('.visualizer-node');
    const filaments = document.querySelectorAll('.filament');
    
    // Orb Elements
    const sphereRole = document.getElementById('visualizer-sphere-role');
    const sphereLabel = document.getElementById('visualizer-sphere-label');
    
    // Card Elements
    const card = document.getElementById('visualizer-card');
    const cardIcon = document.getElementById('visualizer-card-icon');
    const cardTitle = document.getElementById('visualizer-card-title');
    const cardText = document.getElementById('visualizer-card-text');
    const activeAgentLabel = document.getElementById('visualizer-active-agent');
    const impactIndicator = document.getElementById('visualizer-impact-level');

    if (!tabs.length || !card) {
      console.warn('AI Visualizer elements missing from DOM. Skipping simulator initialization.');
      return;
    }

    function activateStage(stageId) {
      const config = visualizerStages[stageId];
      if (!config) return;

      // 1. Toggle Tab buttons
      tabs.forEach(btn => {
        if (parseInt(btn.getAttribute('data-stage')) === stageId) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });

      // 2. Animate and Fade Card Contents
      card.style.opacity = 0;
      card.style.transform = "translateX(10px)";

      setTimeout(() => {
        // Update Right Card Info
        cardTitle.textContent = config.title;
        cardText.textContent = config.summaryText;
        activeAgentLabel.textContent = config.activeAgent;
        
        // Update status indicator inline
        impactIndicator.textContent = config.impactLevel;
        impactIndicator.className = 'status-indicator-inline';
        if (config.impactLevel === 'stable') {
          impactIndicator.classList.add('status-stable');
        } else if (config.impactLevel === 'changed') {
          impactIndicator.classList.add('status-changed');
        } else {
          impactIndicator.classList.add('status-review');
        }

        // Update Icon background, color, and SVG
        cardIcon.style.background = config.iconBg;
        cardIcon.style.color = config.iconColor;
        cardIcon.style.borderColor = config.iconColor;
        cardIcon.innerHTML = config.iconSvg;

        // Update classes
        card.className = `visualizer-card active ${config.themeClass}`;
        
        // Fade back in
        card.style.opacity = 1;
        card.style.transform = "translateX(0)";
      }, 200);

      // 3. Update Orb Labels
      sphereRole.style.opacity = 0;
      sphereLabel.style.opacity = 0;
      setTimeout(() => {
        sphereRole.textContent = config.sphereRole;
        sphereLabel.textContent = config.sphereLabel;
        sphereRole.style.opacity = 1;
        sphereLabel.style.opacity = 1;
      }, 150);

      // 4. Update floating Left Nodes active highlight
      leftNodes.forEach((node, index) => {
        const nodeId = index + 1;
        if (config.activeNodes.includes(nodeId)) {
          node.classList.add('active');
        } else {
          node.classList.remove('active');
        }
      });

      // 5. Update SVG filaments
      filaments.forEach(path => {
        const id = path.getAttribute('id');
        const isLeftFilament = id.startsWith('filament-') && !id.startsWith('filament-right-');
        
        if (isLeftFilament) {
          const filamentNumber = parseInt(id.replace('filament-', ''));
          if (config.activeNodes.includes(filamentNumber)) {
            path.classList.add('active');
          } else {
            path.classList.remove('active');
          }
        } else {
          // Right side filaments
          if (config.activeRightFilaments.includes(id)) {
            path.classList.add('active');
          } else {
            path.classList.remove('active');
          }
        }
      });
    }

    // Attach listeners
    tabs.forEach(btn => {
      btn.addEventListener('click', () => {
        const stageId = parseInt(btn.getAttribute('data-stage'));
        activateStage(stageId);
      });
    });

    // Default to Stage 6 (Ongoing Monitoring) as requested and pre-selected in HTML
    activateStage(6);
  }

  // Run initializer
  init();
});
