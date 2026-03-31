function gateway() {
    return {
        tab: 'stats',
        summary: {},
        modelStats: [],
        providerStats: [],
        requests: [],
        requestsTotal: 0,
        requestsOffset: 0,
        requestsPerPage: 10,
        keys: [],
        newKey: { name: '', provider_filter: '', model_filter: '', token_limit: '' },
        createdKey: null,

        // Model management
        modelsList: [],
        providersList: [],
        availableProviders: [],
        showProviderModal: false,
        showModelModal: false,
        showAddProviderModal: false,
        showAddModelModal: false,
        editingProviderName: null,
        editingModelName: null,
        providerForm: { name: '', base_url: '', api_key: '' },
        modelForm: { name: '', provider: '', input: '', output: '', cache_read: '' },

        // Request detail modal
        requestDetail: null,
        detailBodyTab: 'request',

        // Provider health
        providerHealth: {},
        providerHealthLoading: false,

        // Chart state
        chartMode: 'daily',   // 'daily' | 'hourly'
        chartDays: 7,
        chartInstances: {},
        dailyStatsData: null,
        selectedModel: '',

        // Auth state
        authenticated: false,
        adminKey: '',
        loginKey: '',
        loginError: '',
        loginLoading: false,
        showLoginKey: false,
        _refreshInterval: null,

        async init() {
            const saved = localStorage.getItem('gateway_admin_key');
            if (saved) {
                this.adminKey = saved;
                const valid = await this.verifyKey(saved);
                if (valid) {
                    this.authenticated = true;
                    await this.loadAll();
                    this._refreshInterval = setInterval(() => this.loadAll(), 10000);
                    return;
                }
                // Saved key is invalid, clear it
                localStorage.removeItem('gateway_admin_key');
                this.adminKey = '';
            }
            // Not authenticated — focus the login input after Alpine renders
            this.$nextTick(() => {
                if (this.$refs.loginInput) this.$refs.loginInput.focus();
            });
        },

        async verifyKey(key) {
            try {
                const r = await fetch('/api/auth/verify', {
                    method: 'POST',
                    headers: {
                        'Authorization': 'Bearer ' + key,
                        'Content-Type': 'application/json',
                    },
                });
                return r.ok;
            } catch {
                return false;
            }
        },

        async login() {
            this.loginError = '';
            this.loginLoading = true;
            const key = this.loginKey.trim();
            if (!key) {
                this.loginError = 'Please enter an admin key';
                this.loginLoading = false;
                return;
            }
            const valid = await this.verifyKey(key);
            this.loginLoading = false;
            if (valid) {
                this.adminKey = key;
                localStorage.setItem('gateway_admin_key', key);
                this.authenticated = true;
                this.loginKey = '';
                this.loginError = '';
                await this.loadAll();
                this._refreshInterval = setInterval(() => this.loadAll(), 10000);
            } else {
                this.loginError = 'Invalid admin key';
            }
        },

        signOut() {
            this.authenticated = false;
            this.adminKey = '';
            localStorage.removeItem('gateway_admin_key');
            if (this._refreshInterval) clearInterval(this._refreshInterval);
            this.summary = {};
            this.modelStats = [];
            this.providerStats = [];
            this.requests = [];
            this.keys = [];
            this.loginKey = '';
            this.loginError = '';
            this.$nextTick(() => {
                if (this.$refs.loginInput) this.$refs.loginInput.focus();
            });
        },

        headers() {
            return {
                'Authorization': 'Bearer ' + this.adminKey,
                'Content-Type': 'application/json',
            };
        },

        // Wrapper for API fetches that handles 401 by showing login
        async apiFetch(url, options = {}) {
            options.headers = { ...this.headers(), ...(options.headers || {}) };
            const r = await fetch(url, options);
            if (r.status === 401 || r.status === 403) {
                this.signOut();
                throw new Error('Unauthorized');
            }
            return r;
        },

        async loadAll() {
            const promises = [
                this.loadSummary(),
                this.loadModelStats(),
                this.loadProviderStats(),
                this.loadRequests(),
                this.loadKeys(),
            ];
            promises.push(this.loadDailyStats());
            if (Object.keys(this.providerHealth).length === 0) {
                promises.push(this.checkProviderHealth());
            }
            if (this.tab === 'models') {
                promises.push(this.loadModels());
            }
            await Promise.all(promises);
        },

        async loadSummary() {
            try {
                const r = await this.apiFetch('/api/stats/summary');
                if (r.ok) this.summary = await r.json();
            } catch {}
        },

        async loadModelStats() {
            try {
                const r = await this.apiFetch('/api/stats/models');
                if (r.ok) this.modelStats = (await r.json()).models || [];
            } catch {}
        },

        async loadProviderStats() {
            try {
                const r = await this.apiFetch('/api/stats/providers');
                if (r.ok) this.providerStats = (await r.json()).providers || [];
            } catch {}
        },

        async checkProviderHealth() {
            this.providerHealthLoading = true;
            try {
                const r = await this.apiFetch('/api/health/providers');
                if (r.ok) this.providerHealth = (await r.json()).providers || {};
            } catch {}
            this.providerHealthLoading = false;
        },

        async loadChartData() {
            try {
                const params = new URLSearchParams();
                if (this.selectedModel) params.append('model', this.selectedModel);
                
                if (this.chartMode === 'hourly') {
                    // Last 6 hours of hourly data
                    const from = new Date();
                    from.setHours(from.getHours() - 6);
                    const dateFrom = from.toISOString().slice(0, 10);
                    params.append('date_from', dateFrom);
                    const r = await this.apiFetch('/api/stats/hourly?' + params.toString());
                    if (!r.ok) return;
                    const hourly = (await r.json()).hourly || [];
                    this._renderChartsHourly(hourly);
                } else {
                    const from = new Date();
                    from.setDate(from.getDate() - this.chartDays);
                    const dateFrom = from.toISOString().slice(0, 10);
                    params.append('date_from', dateFrom);
                    const r = await this.apiFetch('/api/stats/daily?' + params.toString());
                    if (!r.ok) return;
                    this.dailyStatsData = (await r.json()).daily || [];
                    this._renderChartsDaily(this.dailyStatsData);
                }
            } catch {}
        },

        async loadDailyStats() {
            // Backwards compat — called from loadAll for refresh
            return this.loadChartData();
        },

     _renderChartsDaily(data) {
            // Aggregate by date
            const dayMap = {};
            const now = new Date();
            for (let i = this.chartDays - 1; i >= 0; i--) {
                const d = new Date(now);
                d.setDate(d.getDate() - i);
                const key = d.toISOString().slice(0, 10);
                dayMap[key] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0 };
            }
            for (const row of data) {
                if (!dayMap[row.date]) dayMap[row.date] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0 };
                dayMap[row.date].requests += row.request_count || 0;
                dayMap[row.date].input_tokens += row.input_tokens || 0;
                dayMap[row.date].output_tokens += row.output_tokens || 0;
                dayMap[row.date].cost += row.cost || 0;
            }
            const dates = Object.keys(dayMap).sort();
            const labels = dates.map(d => {
                const dt = new Date(d + 'T00:00:00');
                return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            });

            // Chart 1: Token Volume (stacked input/output)
            const tokEl = document.getElementById('chartTokens');
            if (tokEl) {
                if (this.chartInstances.tokens) this.chartInstances.tokens.destroy();
                this.chartInstances.tokens = new ApexCharts(tokEl, {
                    chart: {
                        type: 'bar',
                        height: '100%',
                        background: 'transparent',
                        toolbar: { show: false },
                        fontFamily: 'inherit',
                        stacked: true,
                        animations: { enabled: false },
                    },
                    series: [
                        {
                            name: 'Input Tokens (K)',
                            data: dates.map(d => (dayMap[d].input_tokens || 0) / 1000),
                        },
                        {
                            name: 'Output Tokens (K)',
                            data: dates.map(d => (dayMap[d].output_tokens || 0) / 1000),
                        },
                    ],
                    colors: ['#3b82f6', '#a78bfa'],
                    plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } },
                    dataLabels: { enabled: false },
                    xaxis: {
                        categories: labels,
                        axisBorder: { show: false },
                        axisTicks: { show: false },
                        labels: { style: { color: '#9ca3af', fontSize: '10px' } },
                    },
                    yaxis: {
                        title: { text: 'Tokens (K)', style: { color: '#9ca3af', fontSize: '11px' } },
                        labels: { style: { color: '#9ca3af', fontSize: '10px' } },
                    },
                    grid: { borderColor: 'rgba(255,255,255,0.06)', strokeDashArray: 3 },
                    legend: { labels: { colors: '#d1d5db' }, fontSize: '11px' },
                    tooltip: { theme: 'dark' },
                    theme: { mode: 'dark' },
                });
                this.chartInstances.tokens.render();
            }

            // Chart 2: Cost over time (line chart)
            const costEl = document.getElementById('chartCost');
            if (costEl) {
                if (this.chartInstances.cost) this.chartInstances.cost.destroy();
                this.chartInstances.cost = new ApexCharts(costEl, {
                    chart: {
                        type: 'area',
                        height: '100%',
                        background: 'transparent',
                        toolbar: { show: false },
                        fontFamily: 'inherit',
                        animations: { enabled: false },
                    },
                    series: [
                        {
                            name: 'Cost ($)',
                            data: dates.map(d => dayMap[d].cost),
                        },
                    ],
                    colors: ['#f87171'],
                    fill: {
                        type: 'gradient',
                        gradient: {
                            shadeIntensity: 0.5,
                            opacityFrom: 0.3,
                            opacityTo: 0.05,
                        },
                    },
                    stroke: { width: 2, curve: 'smooth' },
                    dataLabels: { enabled: false },
                    xaxis: {
                        categories: labels,
                        axisBorder: { show: false },
                        axisTicks: { show: false },
                        labels: { style: { color: '#9ca3af', fontSize: '10px' } },
                    },
                    yaxis: {
                        title: { text: 'Cost ($)', style: { color: '#9ca3af', fontSize: '11px' } },
                        labels: { 
                            style: { color: '#9ca3af', fontSize: '10px' },
                            formatter: v => '$' + v.toFixed(4)
                        },
                    },
                    grid: { borderColor: 'rgba(255,255,255,0.06)', strokeDashArray: 3 },
                    legend: { labels: { colors: '#d1d5db' }, fontSize: '11px' },
                    tooltip: { theme: 'dark' },
                    theme: { mode: 'dark' },
                });
                this.chartInstances.cost.render();
            }
        },

      _renderChartsHourly(data) {
            // Build hour map for last 6 hours
            const hourMap = {};
            const now = new Date();
            for (let i = 5; i >= 0; i--) {
                const d = new Date(now);
                d.setHours(d.getHours() - i);
                const key = d.getFullYear() + '-' +
                    String(d.getMonth() + 1).padStart(2, '0') + '-' +
                    String(d.getDate()).padStart(2, '0') + ' ' +
                    String(d.getHours()).padStart(2, '0') + ':00';
                hourMap[key] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0 };
            }
            for (const row of data) {
                if (!hourMap[row.hour]) hourMap[row.hour] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0 };
                hourMap[row.hour].requests += row.request_count || 0;
                hourMap[row.hour].input_tokens += row.input_tokens || 0;
                hourMap[row.hour].output_tokens += row.output_tokens || 0;
                hourMap[row.hour].cost += row.cost || 0;
            }
            const hours = Object.keys(hourMap).sort();
            const labels = hours.map(h => {
                const parts = h.split(' ');
                const d = new Date(parts[0] + 'T' + parts[1]);
                return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
                       d.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true });
            });

            // Common chart options builder
            const baseChart = {
                height: '100%',
                background: 'transparent',
                toolbar: { show: false },
                fontFamily: 'inherit',
                animations: { enabled: false },
            };
            const baseXaxis = {
                categories: labels,
                axisBorder: { show: false },
                axisTicks: { show: false },
                labels: { style: { color: '#9ca3af', fontSize: '10px' }, rotate: -45, rotateAlways: true },
            };
            const baseGrid = { borderColor: 'rgba(255,255,255,0.06)', strokeDashArray: 3 };
            const baseLegend = { labels: { colors: '#d1d5db' }, fontSize: '11px' };
            const baseTheme = { mode: 'dark' };

            // Chart 1: Token Volume (stacked input/output)
            const tokEl = document.getElementById('chartTokens');
            if (tokEl) {
                if (this.chartInstances.tokens) this.chartInstances.tokens.destroy();
                this.chartInstances.tokens = new ApexCharts(tokEl, {
                    chart: { ...baseChart, type: 'bar', stacked: true },
                    series: [
                        { name: 'Input Tokens (K)', data: hours.map(h => (hourMap[h].input_tokens || 0) / 1000) },
                        { name: 'Output Tokens (K)', data: hours.map(h => (hourMap[h].output_tokens || 0) / 1000) },
                    ],
                    colors: ['#3b82f6', '#a78bfa'],
                    plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } },
                    dataLabels: { enabled: false },
                    xaxis: baseXaxis,
                    yaxis: {
                        title: { text: 'Tokens (K)', style: { color: '#9ca3af', fontSize: '11px' } },
                        labels: { style: { color: '#9ca3af', fontSize: '10px' } },
                    },
                    grid: baseGrid,
                    legend: baseLegend,
                    tooltip: { theme: 'dark' },
                    theme: baseTheme,
                });
                this.chartInstances.tokens.render();
            }

            // Chart 2: Cost over time (area chart)
            const costEl = document.getElementById('chartCost');
            if (costEl) {
                if (this.chartInstances.cost) this.chartInstances.cost.destroy();
                this.chartInstances.cost = new ApexCharts(costEl, {
                    chart: { ...baseChart, type: 'area' },
                    series: [
                        { name: 'Cost ($)', data: hours.map(h => hourMap[h].cost) },
                    ],
                    colors: ['#f87171'],
                    fill: {
                        type: 'gradient',
                        gradient: {
                            shadeIntensity: 0.5,
                            opacityFrom: 0.3,
                            opacityTo: 0.05,
                        },
                    },
                    stroke: { width: 2, curve: 'smooth' },
                    dataLabels: { enabled: false },
                    xaxis: baseXaxis,
                    yaxis: {
                        title: { text: 'Cost ($)', style: { color: '#9ca3af', fontSize: '11px' } },
                        labels: { 
                            style: { color: '#9ca3af', fontSize: '10px' },
                            formatter: v => '$' + v.toFixed(4)
                        },
                    },
                    grid: baseGrid,
                    legend: baseLegend,
                    tooltip: { theme: 'dark' },
                    theme: baseTheme,
                });
                this.chartInstances.cost.render();
            }
        },

        get requestCurrentPage() {
            if (this.requestsPerPage <= 0) return 1;
            return Math.floor(this.requestsOffset / this.requestsPerPage) + 1;
        },
        get requestTotalPages() {
            if (this.requestsPerPage <= 0) return 1;
            return Math.ceil(this.requestsTotal / this.requestsPerPage);
        },
        get requestPages() {
            const current = this.requestCurrentPage;
            const total = this.requestTotalPages;
            if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
            // Show window of 5 pages around current, plus first and last
            const pages = new Set([1, total]);
            const start = Math.max(2, current - 1);
            const end = Math.min(total - 1, current + 1);
            for (let i = start; i <= end; i++) pages.add(i);
            const sorted = [...pages].sort((a, b) => a - b);
            // Insert ellipsis gaps (use -1 as sentinel)
            const result = [];
            for (let i = 0; i < sorted.length; i++) {
                if (i > 0 && sorted[i] - sorted[i - 1] > 1) result.push(-1);
                result.push(sorted[i]);
            }
            return result;
        },

        async loadRequests() {
            try {
                const limit = this.requestsPerPage;
                const r = await this.apiFetch('/api/requests?limit=' + limit + '&offset=' + this.requestsOffset);
                if (r.ok) {
                    const d = await r.json();
                    this.requests = d.requests || [];
                    this.requestsTotal = d.total || 0;
                }
            } catch {}
        },

        async loadKeys() {
            try {
                const r = await this.apiFetch('/admin/keys');
                if (r.ok) this.keys = (await r.json()).keys || [];
            } catch {}
        },

        async createKey() {
            const body = { name: this.newKey.name || 'unnamed' };
            if (this.newKey.provider_filter) body.provider_filter = this.newKey.provider_filter;
            if (this.newKey.model_filter) body.model_filter = this.newKey.model_filter;
            if (this.newKey.token_limit) body.token_limit = parseInt(this.newKey.token_limit);

            try {
                const r = await this.apiFetch('/admin/keys', {
                    method: 'POST',
                    body: JSON.stringify(body),
                });
                if (r.ok) {
                    this.createdKey = await r.json();
                    this.newKey = { name: '', provider_filter: '', model_filter: '', token_limit: '' };
                    await this.loadKeys();
                } else {
                    const err = await r.json();
                    alert(err.detail || 'Failed to create key');
                }
            } catch {}
        },

        async deactivateKey(id) {
            try {
                await this.apiFetch(`/admin/keys/${id}/deactivate`, { method: 'POST' });
                await this.loadKeys();
            } catch {}
        },

        async reactivateKey(id) {
            try {
                await this.apiFetch(`/admin/keys/${id}/reactivate`, { method: 'POST' });
                await this.loadKeys();
            } catch {}
        },

        async deleteKey(id) {
            if (!confirm('Delete this key? This cannot be undone.')) return;
            try {
                await this.apiFetch(`/admin/keys/${id}`, { method: 'DELETE' });
                await this.loadKeys();
            } catch {}
        },

        async showRequestDetail(id) {
            this.detailBodyTab = 'request';
            this.requestDetail = null;
            try {
                const r = await this.apiFetch('/api/requests/' + id);
                if (r.ok) {
                    this.requestDetail = await r.json();
                }
            } catch {}
        },

        formatJson(text) {
            if (!text) return '';
            try {
                return JSON.stringify(JSON.parse(text), null, 2);
            } catch {
                return text;
            }
        },

        formatTokens(n) {
            if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
            if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
            return (n || 0).toString();
        },

        formatTime(iso) {
            if (!iso) return '';
            // Server stores UTC timestamps without Z — append it
            const d = new Date(iso.includes('Z') || iso.includes('+') ? iso : iso + 'Z');
            const now = new Date();
            const diff = now - d;
            if (diff < 60000) return 'just now';
            if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
            if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
            return d.toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
        },

        formatTTFT(ms) {
            if (ms == null) return '—';
            if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
            return ms.toFixed(0) + 'ms';
        },

        formatTPS(tps) {
            if (tps == null) return '—';
            return tps.toFixed(1) + ' tok/s';
        },

        // Model Management Functions
        async loadModels() {
            try {
                const [configResp, providersResp] = await Promise.all([
                    this.apiFetch('/api/config'),
                    this.apiFetch('/api/providers'),
                ]);
                
                if (configResp.ok) {
                    const config = await configResp.json();
                    
                    // Build providers list from server-provided info (includes built-in + custom)
                    let serverProviders = [];
                    if (providersResp.ok) {
                        serverProviders = (await providersResp.json()).providers || [];
                    }
                    
                    this.providersList = serverProviders.map(p => ({
                        name: p.name,
                        base_url: p.base_url || '',
                        has_key: p.has_api_key,
                        is_built_in: p.is_built_in,
                        is_configured: p.is_configured,
                    }));
                    
                    this.availableProviders = this.providersList.map(p => p.name);
                    
                    // Build models list
                    this.modelsList = Object.entries(config.models || {}).map(([name, cfg]) => {
                        if (typeof cfg === 'string') {
                            return { name: name, provider: cfg, input: null, output: null, cache_read: null };
                        }
                        return {
                            name: name,
                            provider: cfg?.provider || '',
                            input: cfg?.input ?? null,
                            output: cfg?.output ?? null,
                            cache_read: cfg?.cache_read ?? null,
                        };
                    });
                }
            } catch {}
        },

        resetProviderForm() {
            this.providerForm = { name: '', base_url: '', api_key: '' };
            this.editingProviderName = null;
        },

        resetModelForm() {
            this.modelForm = { name: '', provider: '', input: '', output: '', cache_read: '' };
            this.editingModelName = null;
        },

        showAddProvider() {
            this.resetProviderForm();
            this.showAddProviderModal = false;
            this.showProviderModal = true;
        },

        showAddModel() {
            this.resetModelForm();
            this.showAddModelModal = false;
            this.showModelModal = true;
        },

        editProvider(name) {
            this.resetProviderForm();
            this.editingProviderName = name;
            this.showProviderModal = true;
            // Load current config
            fetch('/api/config', { headers: { 'Authorization': 'Bearer ' + this.adminKey } })
                .then(r => r.json())
                .then(config => {
                    const provider = config.providers?.[name] || {};
                    this.providerForm = {
                        name: name,
                        base_url: provider?.base_url || '',
                        api_key: provider?.api_key || '',
                    };
                });
        },

        editModel(name) {
            this.resetModelForm();
            this.editingModelName = name;
            this.showModelModal = true;
            // Find model in current list
            const model = this.modelsList.find(m => m.name === name);
            if (model) {
                this.modelForm = {
                    name: model.name,
                    provider: model.provider,
                    input: model.input != null ? model.input : '',
                    output: model.output != null ? model.output : '',
                    cache_read: model.cache_read != null ? model.cache_read : '',
                };
            }
        },

        async saveProvider() {
            if (!this.providerForm.name || !this.providerForm.base_url) {
                alert('Provider name and base URL are required');
                return;
            }

            try {
                // Get current config
                const r = await fetch('/api/config', { headers: { 'Authorization': 'Bearer ' + this.adminKey } });
                const config = await r.json();
                
                // Update providers
                if (!config.providers) config.providers = {};
                const providerConfig = { base_url: this.providerForm.base_url };
                if (this.providerForm.api_key) {
                    providerConfig.api_key = this.providerForm.api_key;
                }
                config.providers[this.providerForm.name] = providerConfig;
                
                // Save
                await this.apiFetch('/api/config', {
                    method: 'POST',
                    body: JSON.stringify(config),
                });
                
                this.showProviderModal = false;
                await this.loadModels();
            } catch (e) {
                alert('Failed to save provider: ' + e.message);
            }
        },

        async saveModel() {
            if (!this.modelForm.name || !this.modelForm.provider) {
                alert('Model name and provider are required');
                return;
            }

            try {
                // Get current config
                const r = await fetch('/api/config', { headers: { 'Authorization': 'Bearer ' + this.adminKey } });
                const config = await r.json();
                
                // Update models
                if (!config.models) config.models = {};
                const priceCfg = { provider: this.modelForm.provider };
                if (this.modelForm.input !== '') priceCfg.input = parseFloat(this.modelForm.input);
                if (this.modelForm.output !== '') priceCfg.output = parseFloat(this.modelForm.output);
                if (this.modelForm.cache_read !== '') priceCfg.cache_read = parseFloat(this.modelForm.cache_read);
                config.models[this.modelForm.name] = priceCfg;
                
                // Save
                await this.apiFetch('/api/config', {
                    method: 'POST',
                    body: JSON.stringify(config),
                });
                
                this.showModelModal = false;
                await this.loadModels();
            } catch (e) {
                alert('Failed to save model: ' + e.message);
            }
        },

        async deleteProvider(name) {
            if (!confirm(`Delete provider "${name}"? This will remove its custom configuration.`)) return;
            try {
                await this.apiFetch(`/api/providers/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadModels();
            } catch (e) {
                alert('Failed to delete provider: ' + e.message);
            }
        },

        async deleteModel(name) {
            if (!confirm(`Delete model "${name}"? This will remove its custom routing.`)) return;
            try {
                await this.apiFetch(`/api/models/${encodeURIComponent(name)}`, { method: 'DELETE' });
                await this.loadModels();
            } catch (e) {
                alert('Failed to delete model: ' + e.message);
            }
        },
    };
}
