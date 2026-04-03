function gateway() {
    return {
        appVersion: '',
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
        _prevTab: null,

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
        modelsLoading: false,
        chartSummary: { requests: 0, cost: 0, latency_ms: 0, cache_rate: 0 },
        _lastHourKey: null,
        _lastDailyKey: null,

        // Performance metrics
        percentiles: { p50: null, p90: null, p95: null, p99: null },
        errorStats: { total_requests: 0, successful_requests: 0, failed_requests: 0, error_rate: 0, errors_by_type: {} },
        
        // Stats reset
        statsResetting: false,

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
                    // Watch for tab changes to charts
                    this.$watch('tab', (newTab) => {
                        if (newTab === 'charts' && this.modelsList.length === 0) {
                            this.loadModels().catch(() => {});
                        }
                    });
                    return;
                }
                // Saved key is invalid, clear it
                localStorage.removeItem('gateway_admin_key');
                this.adminKey = '';
            }
            // Not authenticated - focus the login input after Alpine renders
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
            
            // Call login endpoint to set session cookie
            try {
                const r = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key })
                });
                const data = await r.json();
                
                if (data.success) {
                    this.adminKey = key;
                    localStorage.setItem('gateway_admin_key', key);
                    this.authenticated=true;
                    this.loginKey = '';
                    this.loginError = '';
                    await this.loadAll();
                    this._refreshInterval = setInterval(() => this.loadAll(), 10000);
                } else {
                    this.loginError = data.error || 'Invalid admin key';
                }
            } catch (e) {
                this.loginError = 'Login failed: ' + e.message;
            }
            
            this.loginLoading = false;
        },

        async signOut() {
            // Clear server session cookie
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch {}
            
            this.authenticated=false;
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
                this.loadPercentiles(),
                this.loadErrorStats(),
            ];
            promises.push(this.loadDailyStats());
            
            // Only check provider health if we haven't done it recently
            // Don't wait for it when loading models tab - let it run in background
            if (Object.keys(this.providerHealth).length === 0) {
                this.checkProviderHealth().catch(() => {});
            }
            
            // Load models if needed for charts filter or if on models tab
            if (this.modelsList.length === 0 && (this.tab === 'models' || this.tab === 'charts')) {
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

        confirmResetStats() {
            if (confirm('Are you sure you want to reset all statistics? This will delete all request history and cannot be undone.')) {
                this.resetStats();
            }
        },

        async resetStats() {
            this.statsResetting = true;
            try {
                const r = await this.apiFetch('/api/stats/reset', { method: 'DELETE' });
                if (r.ok) {
                    alert('Statistics reset successfully');
                    // Reload the page to refresh all stats
                    window.location.reload();
                } else {
                    const err = await r.json();
                    alert('Failed to reset stats: ' + (err.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Failed to reset stats: ' + e.message);
            } finally {
                this.statsResetting = false;
            }
        },

        // Handle chart mode change - destroy and recreate charts
        setChartMode(mode, days) {
            if (this.chartMode === mode && !days) return;
            this.chartMode = mode;
            this.chartDays = days || 7;
            // Destroy all charts and reload with new mode
            this._destroyAllCharts();
            this.loadChartData();
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
                    this._lastHourKey = from.toISOString().slice(0, 10) + ' ' + String(from.getUTCHours()).padStart(2, '0') + ':00';
                } else {
                    const from = new Date();
                    from.setDate(from.getDate() - this.chartDays);
                    const dateFrom = from.toISOString().slice(0, 10);
                    params.append('date_from', dateFrom);
                    const r = await this.apiFetch('/api/stats/daily?' + params.toString());
                    if (!r.ok) return;
                    this.dailyStatsData = (await r.json()).daily || [];
                    this._renderChartsDaily(this.dailyStatsData);
                    this._lastDailyKey = dateFrom;
                }
            } catch {}
        },

        // Called when chart mode or time range changes - destroy and recreate charts
        _reloadCharts() {
            this._destroyAllCharts();
            // Re-trigger chart loading with current settings
            if (this.chartMode === 'hourly') {
                const from = new Date();
                from.setHours(from.getHours() - 6);
                const dateFrom = from.toISOString().slice(0, 10);
                this.selectedModel ? this.loadChartData() : this.loadChartData();
            } else {
                this.loadChartData();
            }
        }

        // Check if chart mode or time range changed
        ,_checkChartNeedsReload() {
            const now = new Date();
            if (this.chartMode === 'hourly') {
                // Last 6 hours
                const from = new Date();
                from.setHours(from.getHours() - 6);
                const dateFrom = from.toISOString().slice(0, 10) + ' ' + String(from.getUTCHours()).padStart(2, '0') + ':00';
                return dateFrom !== this._lastHourKey;
            } else {
                // Daily
                const from = new Date();
                from.setDate(from.getDate() - this.chartDays);
                return from.toISOString().slice(0, 10) !== this._lastDailyKey;
            }
        }

        // Initialize chart mode tracking
        ,init() {
            this._lastHourKey = null;
            this._lastDailyKey = null;
            super.init();
        },

        _updateChartData(chartInstance, newSeries) {
            if (chartInstance) {
                chartInstance.updateSeries(newSeries);
            }
        },

        _destroyAllCharts() {
            if (this.chartInstances.tokens) { this.chartInstances.tokens.destroy(); }
            if (this.chartInstances.cost) { this.chartInstances.cost.destroy(); }
            if (this.chartInstances.requests) { this.chartInstances.requests.destroy(); }
            if (this.chartInstances.distribution) { this.chartInstances.distribution.destroy(); }
            Object.assign(this.chartInstances, { tokens: null, cost: null, requests: null, distribution: null });
        },

    async loadDailyStats() {
            // Backwards compat - called from loadAll for refresh
            return this.loadChartData();
        },

 // Handle chart mode change - destroy and recreate charts
        setChartMode(mode, days) {
            if (this.chartMode === mode && !days) return;
            this.chartMode = mode;
            this.chartDays = days || 7;
            // Destroy all charts and reload with new mode
            this._destroyAllCharts();
            this.loadChartData();
        },

        async loadPercentiles() {
            try {
                const r = await this.apiFetch('/api/stats/percentiles');
                if (!r.ok) return;
                this.percentiles = await r.json();
            } catch {}
        },

        async loadErrorStats() {
            try {
                const r = await this.apiFetch('/api/stats/errors');
                if (!r.ok) return;
                this.errorStats = await r.json();
            } catch {}
        },

        _renderChartsDaily(data) {
            // Aggregate by date
            const dayMap = {};
            const now = new Date();
            for (let i = this.chartDays - 1; i >= 0; i--) {
                const d = new Date(now);
                d.setDate(d.getDate() - i);
                const key = d.toISOString().slice(0, 10);
                dayMap[key] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
            }
            for (const row of data) {
                if (!dayMap[row.date]) dayMap[row.date] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
                dayMap[row.date].requests += row.request_count || 0;
                dayMap[row.date].input_tokens += row.input_tokens || 0;
                dayMap[row.date].output_tokens += row.output_tokens || 0;
                dayMap[row.date].cost += row.cost || 0;
                dayMap[row.date].cache_read_tokens += row.cache_read_tokens || 0;
                dayMap[row.date].cache_write_tokens += row.cache_write_tokens || 0;
            }
            const dates = Object.keys(dayMap).sort();
            const labels = dates.map(d => {
                const dt = new Date(d + 'T00:00:00');
                return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            });

            // Chart 1: Token Volume (stacked)
            const tokEl = document.getElementById('chartTokens');
            if (tokEl) {
                const billable = dates.map(d => Math.max(0, (dayMap[d].input_tokens || 0) - (dayMap[d].cache_read_tokens || 0)));
                const cached = dates.map(d => dayMap[d].cache_read_tokens || 0);
                const output = dates.map(d => dayMap[d].output_tokens || 0);
                
                const newSeries = [
                    { name: 'Fresh Input', data: billable },
                    { name: 'Cached', data: cached },
                    { name: 'Output', data: output },
                ];
                
                if (this.chartInstances.tokens) {
                    // Update existing chart without destroying (prevents animation reset)
                    this.chartInstances.tokens.updateSeries(newSeries);
                } else {
                    // Create new chart
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
                        series: newSeries,
                        colors: ['#3b82f6', '#10b981', '#8b5cf6'],
                        plotOptions: { 
                            bar: { 
                                borderRadius: 4, 
                                columnWidth: '65%',
                                distributed: false
                            } 
                        },
                        dataLabels: {
                            enabled: true,
                            formatter: (val) => val > 500 ? (val/1000).toFixed(1) + 'K' : val,
                            style: { fontSize: '9px', colors: ['#fff', '#fff', '#fff'] }
                        },
                        xaxis: {
                            categories: labels,
                            axisBorder: { show: false },
                            axisTicks: { show: false },
                            labels: { style: { color: '#9ca3af', fontSize: '9px' } },
                        },
                        yaxis: {
                            labels: { 
                                style: { color: '#9ca3af', fontSize: '9px' },
                                formatter: (val) => (val/1000).toFixed(1) + 'K'
                            },
                        },
                        grid: { borderColor: 'rgba(255,255,255,0.08)', strokeDashArray: 3 },
                        legend: { show: false },
                        tooltip: {
                            theme: 'dark',
                            y: {
                                formatter: (val) => val.toLocaleString() + ' tokens'
                            }
                        },
                        theme: { mode: 'dark' },
                    });
                    this.chartInstances.tokens.render();
                }
            }

            // Chart 2: Cost over time
            const costEl = document.getElementById('chartCost');
            if (costEl) {
                const costData = dates.map(d => dayMap[d].cost);
                const newCostSeries = [{ name: 'Cost', data: costData }];
                
                if (this.chartInstances.cost) {
                    this.chartInstances.cost.updateSeries(newCostSeries);
                } else {
                    this.chartInstances.cost = new ApexCharts(costEl, {
                        chart: {
                            type: 'area',
                            height: '100%',
                            background: 'transparent',
                            toolbar: { show: false },
                            fontFamily: 'inherit',
                            animations: { enabled: false },
                        },
                        series: newCostSeries,
                        colors: ['#f43f5e'],
                        fill: {
                            type: 'gradient',
                            gradient: {
                                shadeIntensity: 1,
                                opacityFrom: 0.4,
                                opacityTo: 0.05,
                                stops: [0, 90, 100]
                            },
                        },
                        stroke: { width: 2, curve: 'smooth' },
                        dataLabels: {
                            enabled: true,
                            enabledOnSeries: ['Cost'],
                            formatter: (val) => '$' + val.toFixed(4),
                            style: { fontSize: '9px', colors: ['#fff'] }
                        },
                        xaxis: {
                            categories: labels,
                            axisBorder: { show: false },
                            axisTicks: { show: false },
                            labels: { style: { color: '#9ca3af', fontSize: '9px' } },
                        },
                        yaxis: {
                            labels: { 
                                style: { color: '#9ca3af', fontSize: '9px' },
                                formatter: v => '$' + v.toFixed(4)
                            },
                        },
                        grid: { borderColor: 'rgba(255,255,255,0.08)', strokeDashArray: 3 },
                        legend: { show: false },
                        tooltip: {
                            theme: 'dark',
                            y: { formatter: v => '$' + v.toFixed(4) }
                        },
                        theme: { mode: 'dark' },
                    });
                    this.chartInstances.cost.render();
                }
            }

            // Chart 3: Request Volume
            const reqEl = document.getElementById('chartRequests');
            if (reqEl) {
                const reqData = dates.map(d => dayMap[d].requests);
                const newReqSeries = [{ name: 'Requests', data: reqData }];
                
                if (this.chartInstances.requests) {
                    this.chartInstances.requests.updateSeries(newReqSeries);
                } else {
                    this.chartInstances.requests = new ApexCharts(reqEl, {
                        chart: {
                            type: 'bar',
                            height: '100%',
                            background: 'transparent',
                            toolbar: { show: false },
                            fontFamily: 'inherit',
                            animations: { enabled: false },
                        },
                        series: newReqSeries,
                        colors: ['#f59e0b'],
                        plotOptions: { 
                            bar: { 
                                borderRadius: 4, 
                                columnWidth: '65%',
                                barHeight: '100%'
                            } 
                        },
                        dataLabels: {
                            enabled: true,
                            formatter: (val) => val,
                            style: { fontSize: '9px', colors: ['#fff'] }
                        },
                        xaxis: {
                            categories: labels,
                            axisBorder: { show: false },
                            axisTicks: { show: false },
                            labels: { style: { color: '#9ca3af', fontSize: '9px' } },
                        },
                        yaxis: {
                            labels: { 
                                style: { color: '#9ca3af', fontSize: '9px' },
                                formatter: (val) => Math.round(val)
                            },
                        },
                        grid: { borderColor: 'rgba(255,255,255,0.08)', strokeDashArray: 3 },
                        legend: { show: false },
                        tooltip: {
                            theme: 'dark',
                            y: { formatter: (val) => val.toLocaleString() + ' requests' }
                        },
                        theme: { mode: 'dark' },
                    });
                    this.chartInstances.requests.render();
                }
            }

            // Chart 4: Token Distribution (Donut)
            const distEl = document.getElementById('chartDistribution');
            if (distEl) {
                // Calculate totals
                let totalInput = 0, totalCached = 0, totalOutput = 0, totalRequests = 0, totalCost = 0;
                dates.forEach(d => {
                    totalInput += dayMap[d].input_tokens || 0;
                    totalCached += dayMap[d].cache_read_tokens || 0;
                    totalOutput += dayMap[d].output_tokens || 0;
                    totalRequests += dayMap[d].requests || 0;
                    totalCost += dayMap[d].cost || 0;
                });
                const total = totalInput + totalOutput;
                const fresh = totalInput - totalCached;
                const newSeries = [fresh, totalCached, totalOutput];
                
                // Update summary stats
                this.chartSummary = {
                    requests: totalRequests,
                    cost: totalCost,
                    latency_ms: 0, // Not available in daily stats
                    cache_rate: totalInput > 0 ? (totalCached / totalInput * 100) : 0
                };
                
                if (this.chartInstances.distribution) {
                    this.chartInstances.distribution.updateSeries(newSeries);
                } else {
                    this.chartInstances.distribution = new ApexCharts(distEl, {
                        chart: {
                            type: 'donut',
                            height: '100%',
                            background: 'transparent',
                            toolbar: { show: false },
                            fontFamily: 'inherit',
                            animations: { enabled: false },
                        },
                        series: newSeries,
                        labels: ['Fresh', 'Cached', 'Output'],
                        colors: ['#3b82f6', '#10b981', '#8b5cf6'],
                        dataLabels: {
                            enabled: true,
                            formatter: (val, opts) => {
                                const pct = (val / total * 100).toFixed(1);
                                return pct > 5 ? pct + '%' : '';
                            },
                            style: { fontSize: '10px', colors: ['#fff', '#fff', '#fff'] },
                        },
                        legend: { show: false },
                        tooltip: {
                            theme: 'dark',
                            y: {
                                formatter: (val) => {
                                    return val.toLocaleString() + ' tokens (' + (val/total*100).toFixed(1) + '%)';
                                }
                            }
                        },
                        theme: { mode: 'dark' },
                        plotOptions: {
                            pie: {
                                donut: {
                                    size: '70%',
                                    labels: {
                                        show: true,
                                        showAlways: true,
                                        name: { show: true, fontSize: '11px', color: '#9ca3af' },
                                        value: {
                                            show: true,
                                            fontSize: '16px',
                                            fontWeight: 600,
                                            color: '#f3f4f6',
                                            formatter: (val) => (val/1000).toFixed(1) + 'K'
                                        },
                                        total: {
                                            show: true,
                                            label: 'Total',
                                            fontSize: '11px',
                                            color: '#9ca3af',
                                            formatter: () => (total/1000).toFixed(1) + 'K'
                                        }
                                    }
                                }
                            }
                        }
                    });
                    this.chartInstances.distribution.render();
                }
            }
        },

      _renderChartsHourly(data) {
            // Build hour map for last 6 hours
            const hourMap = {};
            const now = new Date();
            for (let i = 5; i >= 0; i--) {
                const d = new Date(now);
                d.setHours(d.getHours() - i);
                // Build UTC key to match server's strftime('%Y-%m-%d %H:00') output
                const key = d.getUTCFullYear() + '-' +
                    String(d.getUTCMonth() + 1).padStart(2, '0') + '-' +
                    String(d.getUTCDate()).padStart(2, '0') + ' ' +
                    String(d.getUTCHours()).padStart(2, '0') + ':00';
                hourMap[key] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
            }
            for (const row of data) {
                if (!hourMap[row.hour]) hourMap[row.hour] = { requests: 0, input_tokens: 0, output_tokens: 0, cost: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
                hourMap[row.hour].requests += row.request_count || 0;
                hourMap[row.hour].input_tokens += row.input_tokens || 0;
                hourMap[row.hour].output_tokens += row.output_tokens || 0;
                hourMap[row.hour].cost += row.cost || 0;
                hourMap[row.hour].cache_read_tokens += row.cache_read_tokens || 0;
                hourMap[row.hour].cache_write_tokens += row.cache_write_tokens || 0;
            }
            const hours = Object.keys(hourMap).sort();
            const labels = hours.map(h => {
                const parts = h.split(' ');
                // Server returns UTC hour strings like "2026-04-02 15:00" - append 'Z' so JS parses as UTC
                const d = new Date(parts[0] + 'T' + parts[1] + ':00Z');
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

            // Chart 1: Token Volume (stacked)
            const tokEl = document.getElementById('chartTokens');
            if (tokEl) {
                const billable = hours.map(h => Math.max(0, (hourMap[h].input_tokens || 0) - (hourMap[h].cache_read_tokens || 0)));
                const cached = hours.map(h => hourMap[h].cache_read_tokens || 0);
                const output = hours.map(h => hourMap[h].output_tokens || 0);
                const newSeries = [
                    { name: 'Fresh Input', data: billable },
                    { name: 'Cached', data: cached },
                    { name: 'Output', data: output },
                ];
                
                if (this.chartInstances.tokens) {
                    this.chartInstances.tokens.updateSeries(newSeries);
                } else {
                    this.chartInstances.tokens = new ApexCharts(tokEl, {
                        chart: { ...baseChart, type: 'bar', stacked: true },
                        series: newSeries,
                        colors: ['#3b82f6', '#10b981', '#8b5cf6'],
                        plotOptions: { 
                            bar: { 
                                borderRadius: 4, 
                                columnWidth: '65%',
                                distributed: false
                            } 
                        },
                        dataLabels: {
                            enabled: true,
                            formatter: (val) => val > 500 ? (val/1000).toFixed(1) + 'K' : val,
                            style: { fontSize: '9px', colors: ['#fff', '#fff', '#fff'] }
                        },
                    xaxis: baseXaxis,
                    yaxis: {
                        labels: { 
                            style: { color: '#9ca3af', fontSize: '9px' },
                            formatter: (val) => (val/1000).toFixed(1) + 'K'
                        },
                    },
                    grid: baseGrid,
                    legend: { show: false },
                    tooltip: {
                        theme: 'dark',
                        y: { formatter: (val) => val.toLocaleString() + ' tokens' }
                    },
                    theme: baseTheme,
                });
                    this.chartInstances.tokens.render();
                }
            }

            // Chart 2: Cost over time
            const costEl = document.getElementById('chartCost');
            if (costEl) {
                const costData = hours.map(h => hourMap[h].cost);
                const newCostSeries = [{ name: 'Cost', data: costData }];
                
                if (this.chartInstances.cost) {
                    this.chartInstances.cost.updateSeries(newCostSeries);
                } else {
                    this.chartInstances.cost = new ApexCharts(costEl, {
                        chart: { ...baseChart, type: 'area' },
                        series: newCostSeries,
                        colors: ['#f43f5e'],
                        fill: {
                            type: 'gradient',
                            gradient: {
                                shadeIntensity: 1,
                                opacityFrom: 0.4,
                                opacityTo: 0.05,
                                stops: [0, 90, 100]
                            },
                        },
                        stroke: { width: 2, curve: 'smooth' },
                        dataLabels: {
                            enabled: true,
                            enabledOnSeries: ['Cost'],
                            formatter: (val) => '$' + val.toFixed(4),
                            style: { fontSize: '9px', colors: ['#fff'] }
                        },
                        xaxis: baseXaxis,
                        yaxis: {
                            labels: { 
                                style: { color: '#9ca3af', fontSize: '9px' },
                                formatter: v => '$' + v.toFixed(4)
                            },
                        },
                        grid: baseGrid,
                        legend: { show: false },
                        tooltip: { theme: 'dark', y: { formatter: v => '$' + v.toFixed(4) } },
                        theme: baseTheme,
                    });
                    this.chartInstances.cost.render();
                }
            }

            // Chart 3: Request Volume
            const reqEl = document.getElementById('chartRequests');
            if (reqEl) {
                const reqData = hours.map(h => hourMap[h].requests);
                const newReqSeries = [{ name: 'Requests', data: reqData }];
                
                if (this.chartInstances.requests) {
                    this.chartInstances.requests.updateSeries(newReqSeries);
                } else {
                    this.chartInstances.requests = new ApexCharts(reqEl, {
                        chart: { ...baseChart, type: 'bar' },
                        series: newReqSeries,
                        colors: ['#f59e0b'],
                        plotOptions: { 
                            bar: { 
                                borderRadius: 4, 
                                columnWidth: '65%',
                                barHeight: '100%'
                            } 
                        },
                        dataLabels: {
                            enabled: true,
                            formatter: (val) => val,
                            style: { fontSize: '9px', colors: ['#fff'] }
                        },
                        xaxis: baseXaxis,
                        yaxis: {
                            labels: { 
                                style: { color: '#9ca3af', fontSize: '9px' },
                                formatter: (val) => Math.round(val)
                            },
                        },
                        grid: baseGrid,
                        legend: { show: false },
                        tooltip: { theme: 'dark', y: { formatter: (val) => val.toLocaleString() + ' requests' } },
                        theme: baseTheme,
                    });
                    this.chartInstances.requests.render();
                }
            }

            // Chart 4: Distribution (Donut)
            const distEl = document.getElementById('chartDistribution');
            if (distEl) {
                let totalInput = 0, totalCached = 0, totalOutput = 0, totalRequests = 0, totalCost = 0;
                hours.forEach(h => {
                    totalInput += hourMap[h].input_tokens || 0;
                    totalCached += hourMap[h].cache_read_tokens || 0;
                    totalOutput += hourMap[h].output_tokens || 0;
                    totalRequests += hourMap[h].requests || 0;
                    totalCost += hourMap[h].cost || 0;
                });
                const total = totalInput + totalOutput;
                const fresh = totalInput - totalCached;
                const newSeries = [fresh, totalCached, totalOutput];
                
                // Update summary
                this.chartSummary = {
                    requests: totalRequests,
                    cost: totalCost,
                    latency_ms: 0,
                    cache_rate: totalInput > 0 ? (totalCached / totalInput * 100) : 0
                };
                
                if (this.chartInstances.distribution) {
                    this.chartInstances.distribution.updateSeries(newSeries);
                } else {
                    this.chartInstances.distribution = new ApexCharts(distEl, {
                        chart: { ...baseChart, type: 'donut' },
                        series: newSeries,
                        labels: ['Fresh', 'Cached', 'Output'],
                        colors: ['#3b82f6', '#10b981', '#8b5cf6'],
                        dataLabels: {
                            enabled: true,
                            formatter: (val) => {
                                const pct = (val / total * 100).toFixed(1);
                                return pct > 5 ? pct + '%' : '';
                            },
                            style: { fontSize: '10px', colors: ['#fff', '#fff', '#fff'] },
                        },
                        legend: { show: false },
                        tooltip: { theme: 'dark', y: { formatter: (val) => val.toLocaleString() + ' tokens' } },
                        theme: baseTheme,
                        plotOptions: {
                            pie: {
                                donut: {
                                    size: '70%',
                                    labels: {
                                        show: true,
                                        showAlways: true,
                                        name: { show: true, fontSize: '11px', color: '#9ca3af' },
                                        value: {
                                            show: true,
                                            fontSize: '16px',
                                            fontWeight: 600,
                                            color: '#f3f4f6',
                                            formatter: (val) => (val/1000).toFixed(1) + 'K'
                                        },
                                        total: {
                                            show: true,
                                            label: 'Total',
                                            fontSize: '11px',
                                            color: '#9ca3af',
                                            formatter: () => (total/1000).toFixed(1) + 'K'
                                        }
                                    }
                                }
                            }
                        }
                    });
                    this.chartInstances.distribution.render();
                }
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

        formatCacheSavings() {
            // Calculate savings from cached tokens
            const cached = this.summary.total_cache_read_tokens || 0;
            if (cached === 0) return '$0.00';
            
            // Estimate savings: assume 80% discount on cached tokens
            // This is an approximation based on typical provider pricing
            const avgInputPrice = 2.50 / 1_000_000; // $2.50 per 1M tokens (average)
            const avgCachePrice = 0.50 / 1_000_000;  // $0.50 per 1M tokens (average cached)
            const savingsPerToken = avgInputPrice - avgCachePrice;
            const totalSavings = cached * savingsPerToken;
            
            return '$' + totalSavings.toFixed(2);
        },

        formatTime(iso) {
            if (!iso) return '';
            // Server stores UTC timestamps without Z - append it
            const d = new Date(iso.includes('Z') || iso.includes('+') ? iso : iso + 'Z');
            const now = new Date();
            const diff = now - d;
            if (diff < 60000) return 'just now';
            if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
            if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
            // Use browser's local timezone automatically
            return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
        },

        formatTTFT(ms) {
            if (ms == null) return '-';
            if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
            return ms.toFixed(0) + 'ms';
        },

        formatTPS(tps) {
            if (tps == null) return '-';
            return tps.toFixed(1) + ' tok/s';
        },

        // Model Management Functions
        async loadModelsThenSwitch() {
            // If already on models tab and data is loaded, just switch
            if (this.tab === 'models' && this.modelsList.length > 0) {
                return;
            }
            
            // Start loading
            this.modelsLoading = true;
            
            // Load data
            await this.loadModels();
            
            // Switch to models tab
            this.tab = 'models';
        },
        
        async loadModels() {
            try {
                this.modelsLoading = true;
                
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
            } catch {} finally {
                this.modelsLoading = false;
            }
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
