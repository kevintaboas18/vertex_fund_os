<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vertex Fund OS | Terminal</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background-color: #0B0E14; color: #e5e7eb; font-family: 'Inter', sans-serif; }
        .bg-panel { background-color: #11141D; }
        .border-panel { border-color: #1F2532; }
        .custom-scroll::-webkit-scrollbar { width: 6px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #1F2532; border-radius: 4px; }

        /* Analysis Tabs */
        .analysis-tab { 
            padding: 8px 20px; 
            font-size: 12px; 
            font-weight: 700; 
            letter-spacing: 0.08em; 
            text-transform: uppercase; 
            border-bottom: 2px solid transparent; 
            color: #6b7280; 
            cursor: pointer; 
            transition: all 0.2s; 
            background: transparent;
            border-top: none;
            border-left: none;
            border-right: none;
        }
        .analysis-tab:hover { color: #e5e7eb; }
        .analysis-tab.active { color: #3b82f6; border-bottom-color: #3b82f6; }

        /* Tab Content */
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* Timeframe pills */
        .tf-pill {
            padding: 4px 12px;
            font-size: 11px;
            font-weight: 700;
            border-radius: 20px;
            cursor: pointer;
            border: 1px solid #1F2532;
            background: transparent;
            color: #6b7280;
            transition: all 0.2s;
        }
        .tf-pill:hover { color: #e5e7eb; border-color: #3b82f6; }
        .tf-pill.active { background: #1e3a5f; color: #3b82f6; border-color: #3b82f6; }

        /* News card */
        .news-card {
            background: #0B0E14;
            border: 1px solid #1F2532;
            border-radius: 12px;
            padding: 14px 16px;
            transition: border-color 0.2s;
        }
        .news-card:hover { border-color: #3b82f6; }

        /* Targets visual bar */
        .target-bar-track { background: #1a1f2e; border-radius: 6px; height: 6px; position: relative; }
        .target-bar-fill { height: 100%; border-radius: 6px; transition: width 0.8s ease; }
    </style>
</head>
<body class="min-h-screen flex flex-col custom-scroll">

    <!-- Loading Screen -->
    <div id="loadingScreen" class="fixed inset-0 bg-[#0B0E14] z-50 flex flex-col items-center justify-center p-6 hidden">
        <div class="max-w-md w-full text-center space-y-6">
            <h1 class="text-3xl font-extrabold text-white tracking-widest uppercase mb-2">VERTEX AI</h1>
            <p class="text-blue-500 text-sm font-medium tracking-wide" id="loadingText">Reviewing market conditions...</p>
            <div class="w-full bg-gray-900 rounded-full h-1">
                <div class="bg-blue-600 h-full w-2/3 animate-pulse"></div>
            </div>
        </div>
    </div>

    <!-- NAV -->
    <nav class="flex items-center justify-between px-6 py-3 border-b border-panel bg-[#0B0E14] sticky top-0 z-40">
        <div class="flex items-center gap-8">
            <div onclick="switchView('homeView')" class="flex items-center gap-2 text-white font-bold text-lg tracking-wide cursor-pointer">
                <div class="w-3 h-3 bg-blue-500 rounded-full"></div> VERTEX AI
            </div>
            <div class="hidden md:flex gap-6 text-sm text-gray-400 font-medium">
                <button onclick="switchView('homeView')" class="hover:text-white transition">Dashboard</button>
                <button onclick="switchView('reportsView')" class="hover:text-white transition">Reports</button>
                <button class="hover:text-white transition flex items-center gap-1">Vertex Credit Division</button>
            </div>
        </div>
        <div class="flex items-center gap-4">
            <div class="text-xs bg-panel border border-panel px-3 py-1.5 rounded-full flex items-center gap-2">
                <span class="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></span>
                <span class="text-gray-400">Core Status:</span> 
                <strong class="text-white">ONLINE</strong>
            </div>
        </div>
    </nav>

    <!-- HOME VIEW -->
    <main id="homeView" class="view-section flex-grow flex flex-col items-center pt-16 px-4 w-full max-w-4xl mx-auto">
        <h1 class="text-3xl font-bold text-white mb-2 text-center">What would you like to analyze?</h1>
        <p class="text-gray-400 text-sm mb-8 text-center">Search a stock, ETF, or crypto for instant data and AI analysis</p>

        <div class="w-full relative mb-6">
            <i data-lucide="search" class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500"></i>
            <input type="text" id="tickerInput" onkeyup="debounceQuoteFetch()" placeholder="Search company or ticker..." class="w-full bg-[#11141D] border border-gray-800 focus:border-blue-500 text-white font-medium px-5 py-4 pl-12 pr-12 rounded-xl outline-none transition-all uppercase text-base">
        </div>
        
        <div id="previewCard" class="w-full bg-[#11141D] border border-panel rounded-2xl p-6 mb-8 hidden transition-all duration-300">
            <div class="flex justify-between items-start mb-6">
                <div class="flex items-center gap-4">
                    <div id="previewLogoBox" class="w-12 h-12 rounded-xl bg-gray-800 border border-gray-700 flex items-center justify-center overflow-hidden">
                        <img id="previewLogo" src="" alt="logo" class="w-full h-full object-contain" onerror="this.src='https://ui-avatars.com/api/?name='+document.getElementById('previewTicker').innerText+'&background=0B0E14&color=3b82f6&font-size=0.4&bold=true'">
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <h3 id="previewTicker" class="text-xl font-bold text-white tracking-wide"></h3>
                            <span id="previewPct" class="text-xs font-bold px-2 py-0.5 rounded-md"></span>
                        </div>
                        <p id="previewName" class="text-sm text-gray-400 mt-0.5"></p>
                    </div>
                </div>
                <div class="text-right">
                    <p id="previewPrice" class="text-2xl font-mono font-black text-white"></p>
                </div>
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 bg-[#0B0E14] p-4 rounded-xl border border-gray-900 mb-6">
                <div><span class="text-[10px] text-gray-500 font-bold uppercase block">Volume</span><span id="mVolume" class="text-sm font-mono font-bold text-gray-200"></span></div>
                <div><span class="text-[10px] text-gray-500 font-bold uppercase block">VWAP</span><span id="mVwap" class="text-sm font-mono font-bold text-gray-200"></span></div>
                <div><span class="text-[10px] text-gray-500 font-bold uppercase block">High</span><span id="mHigh" class="text-sm font-mono font-bold text-gray-200"></span></div>
                <div><span class="text-[10px] text-gray-500 font-bold uppercase block">Low</span><span id="mLow" class="text-sm font-mono font-bold text-gray-200"></span></div>
            </div>

            <button onclick="ejecutarAnalisisAPI()" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-2 transition shadow-lg shadow-blue-500/10 text-sm">
                <i data-lucide="zap" class="w-4 h-4"></i> Run Full AI Analysis
            </button>
        </div>

        <div class="w-full bg-panel border border-panel rounded-2xl p-6 mb-12">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-white font-semibold flex items-center gap-2 text-sm"><i data-lucide="clock" class="w-4 h-4 text-blue-500"></i> Recent Reports</h3>
                <button onclick="switchView('reportsView')" class="text-blue-500 text-xs hover:text-blue-400">View all History ></button>
            </div>
            <div id="homeRecentList" class="space-y-3"></div>
        </div>
    </main>

    <!-- REPORTS VIEW -->
    <main id="reportsView" class="view-section hidden flex-grow w-full max-w-4xl mx-auto p-4">
        <div class="flex items-center justify-between mb-6">
            <h2 class="text-xl font-bold text-white">Report History</h2>
            <button onclick="switchView('homeView')" class="text-sm text-gray-400 hover:text-white flex items-center gap-1"><i data-lucide="arrow-left" class="w-4 h-4"></i> Back</button>
        </div>
        <div id="fullReportsList" class="space-y-3"></div>
    </main>

    <!-- DASHBOARD VIEW — Nueva arquitectura con tabs -->
    <main id="dashboardView" class="view-section hidden flex-grow w-full max-w-5xl mx-auto p-4 flex flex-col gap-0">

        <!-- Header sticky de la empresa -->
        <div class="bg-panel border border-panel p-5 rounded-2xl flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-4">
            <div class="flex items-center gap-4">
                <div class="w-14 h-14 rounded-xl bg-[#0B0E14] border border-panel flex items-center justify-center overflow-hidden">
                    <img id="dashLogo" src="" alt="logo" class="w-full h-full object-contain">
                </div>
                <div>
                    <h2 class="text-2xl font-black text-white uppercase tracking-wider" id="dashHeaderTitle"></h2>
                    <p class="text-xs font-mono text-gray-400 mt-1" id="dashAnalysisTime"></p>
                </div>
            </div>
            <div class="text-right">
                <p class="text-[10px] text-gray-500 uppercase font-bold tracking-wider mb-0.5">Current Price</p>
                <span class="text-3xl font-mono font-bold text-white" id="dashPrice"></span>
            </div>
        </div>

        <!-- TABS de navegación -->
        <div class="bg-panel border border-panel rounded-2xl mb-4 overflow-hidden">
            <div class="flex border-b border-panel px-2 pt-1">
                <button class="analysis-tab active" onclick="switchAnalysisTab('quickTakeTab', this)">
                    ⚡ Quick Take
                </button>
                <button class="analysis-tab" onclick="switchAnalysisTab('noticesTab', this)">
                    📰 Notices
                </button>
                <button class="analysis-tab" onclick="switchAnalysisTab('fullResearchTab', this)">
                    🔬 Full Research
                </button>
            </div>

            <!-- ======================== TAB 1: QUICK TAKE ======================== -->
            <div id="quickTakeTab" class="tab-content active p-6 space-y-6">

                <!-- Quick Take Cards -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-1 flex items-center gap-2">
                        <i data-lucide="zap" class="w-4 h-4"></i> Quick Take
                    </h3>
                    <p class="text-xs text-gray-500 italic mb-4">Los factores clave que mueven la acción ahora mismo.</p>
                    <div class="space-y-3">
                        <div class="bg-[#0B0E14] p-3.5 rounded-xl border border-gray-900">
                            <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider block mb-1">🔥 Biggest Pro</span>
                            <p id="qtBiggestPro" class="text-xs text-gray-200 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-3.5 rounded-xl border border-gray-900">
                            <span class="text-[10px] font-bold text-red-400 uppercase tracking-wider block mb-1">⚠️ Biggest Risk</span>
                            <p id="qtBiggestRisk" class="text-xs text-gray-200 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-3.5 rounded-xl border border-gray-900">
                            <span class="text-[10px] font-bold text-amber-400 uppercase tracking-wider block mb-1">👀 Watch For</span>
                            <p id="qtWatchFor" class="text-xs text-gray-200 leading-relaxed"></p>
                        </div>
                    </div>
                </div>

                <!-- Chart 1M con targets de 12M -->
                <div>
                    <div class="flex items-center justify-between mb-3">
                        <h3 class="text-xs font-bold text-white flex items-center gap-2">
                            <i data-lucide="bar-chart-2" class="w-4 h-4 text-blue-500"></i> Price Chart (1-Month) + 12M Projections
                        </h3>
                    </div>
                    <div class="relative w-full h-64 mb-4">
                        <canvas id="qtChart"></canvas>
                    </div>
                    <!-- Targets 12M inline bajo el chart -->
                    <div class="grid grid-cols-3 gap-3">
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-emerald-400 font-bold block uppercase mb-1">🟢 Bull 12M</span>
                            <span id="qt_bull_12m" class="text-sm font-mono font-black text-emerald-400"></span>
                            <span id="qt_bull_12m_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-amber-400 font-bold block uppercase mb-1">🟡 Base 12M</span>
                            <span id="qt_base_12m" class="text-sm font-mono font-black text-amber-400"></span>
                            <span id="qt_base_12m_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-red-400 font-bold block uppercase mb-1">🔴 Bear 12M</span>
                            <span id="qt_bear_12m" class="text-sm font-mono font-black text-red-400"></span>
                            <span id="qt_bear_12m_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                    </div>
                </div>

                <!-- Company Summary Simple -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-3 flex items-center gap-2">
                        <i data-lucide="info" class="w-4 h-4"></i> About This Company
                    </h3>
                    <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                        <p id="qtCompanySummary" class="text-xs text-gray-300 leading-relaxed"></p>
                    </div>
                </div>

            </div>

            <!-- ======================== TAB 2: NOTICES ======================== -->
            <div id="noticesTab" class="tab-content p-6">
                <div class="flex items-center justify-between mb-1">
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest flex items-center gap-2">
                        <i data-lucide="newspaper" class="w-4 h-4"></i> News Feed — Last 6 Months
                    </h3>
                    <span id="newsCount" class="text-xs text-gray-500 font-mono"></span>
                </div>
                <p class="text-xs text-gray-500 italic mb-5">Todas las noticias de la compañía extraídas directamente de Yahoo Finance.</p>

                <div id="noticesNewsContainer" class="space-y-3">
                    <div class="text-center text-gray-500 text-sm py-8">
                        <i data-lucide="loader" class="w-5 h-5 inline animate-spin mb-2"></i>
                        <p>Loading news...</p>
                    </div>
                </div>
            </div>

            <!-- ======================== TAB 3: FULL RESEARCH ======================== -->
            <div id="fullResearchTab" class="tab-content p-6 space-y-8">

                <!-- VERDICT -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                        <i data-lucide="scale" class="w-4 h-4"></i> Verdict
                    </h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-gray-500 uppercase font-bold block mb-1">Fair Value</span>
                            <span id="vFairValue" class="text-xl font-mono font-black text-emerald-400"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-gray-500 uppercase font-bold block mb-1">Upside PCT</span>
                            <span id="vUpside" class="text-xl font-mono font-black text-blue-400"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-gray-500 uppercase font-bold block mb-1">Current Price</span>
                            <span id="vCurrentPrice" class="text-xl font-mono font-black text-white"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 text-center flex flex-col justify-center items-center">
                            <span class="text-[10px] text-gray-500 uppercase font-bold block mb-1">Recommendation</span>
                            <span id="vRecommendation" class="text-sm font-extrabold px-3 py-1 rounded bg-blue-500/10 text-blue-400 uppercase border border-blue-500/20"></span>
                        </div>
                    </div>

                    <!-- Conviction Score -->
                    <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                        <div class="flex items-center justify-between mb-3">
                            <div class="flex items-center gap-2">
                                <i data-lucide="shield-check" class="w-4 h-4 text-blue-500"></i>
                                <span class="text-xs font-bold text-white uppercase tracking-wider">Conviction Score</span>
                            </div>
                            <div class="flex items-center gap-3">
                                <div class="w-28 h-2 bg-gray-900 rounded-full overflow-hidden border border-gray-800">
                                    <div id="convictionBar" class="h-full bg-gradient-to-r from-blue-600 to-emerald-500 transition-all duration-1000"></div>
                                </div>
                                <span id="convictionScoreBadge" class="font-mono font-black text-lg text-white"></span>
                            </div>
                        </div>
                        <p id="convictionReason" class="text-xs text-gray-300 leading-relaxed"></p>
                    </div>
                </div>

                <!-- CHART con selector de timeframe -->
                <div>
                    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
                        <h3 class="text-xs font-bold text-white flex items-center gap-2">
                            <i data-lucide="bar-chart-2" class="w-4 h-4 text-blue-500"></i> Market Price Chart + Projections
                        </h3>
                        <div class="flex items-center gap-2 flex-wrap">
                            <button class="tf-pill active" onclick="changeTimeframe('7d', this)">7D</button>
                            <button class="tf-pill" onclick="changeTimeframe('1mo', this)">30D</button>
                            <button class="tf-pill" onclick="changeTimeframe('3mo', this)">3M</button>
                            <button class="tf-pill" onclick="changeTimeframe('6mo', this)">6M</button>
                            <button class="tf-pill" onclick="changeTimeframe('1y', this)">12M</button>
                        </div>
                    </div>
                    <div class="relative w-full h-64 mb-4">
                        <canvas id="frChart"></canvas>
                    </div>
                    <!-- Targets dinámicos por timeframe -->
                    <div class="grid grid-cols-3 gap-3">
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-emerald-400 font-bold block uppercase mb-1" id="frBullLabel">🟢 Bull</span>
                            <span id="fr_bull" class="text-sm font-mono font-black text-emerald-400"></span>
                            <span id="fr_bull_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-amber-400 font-bold block uppercase mb-1" id="frBaseLabel">🟡 Base</span>
                            <span id="fr_base" class="text-sm font-mono font-black text-amber-400"></span>
                            <span id="fr_base_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-red-400 font-bold block uppercase mb-1" id="frBearLabel">🔴 Bear</span>
                            <span id="fr_bear" class="text-sm font-mono font-black text-red-400"></span>
                            <span id="fr_bear_pct" class="text-[10px] text-gray-500 block"></span>
                        </div>
                    </div>
                </div>

                <!-- FULL RESEARCH DETAIL -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                        <i data-lucide="microscope" class="w-4 h-4"></i> Full Research
                    </h3>
                    <div class="space-y-4">
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                                <span class="text-[10px] text-gray-500 font-bold block mb-1 uppercase">Financial Summary</span>
                                <p id="frFinancialSummary" class="text-xs text-gray-300 leading-relaxed"></p>
                            </div>
                            <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                                <span class="text-[10px] text-gray-500 font-bold block mb-1 uppercase">Year-over-Year (YoY) Growth</span>
                                <p id="frYoY" class="text-xs text-gray-300 leading-relaxed"></p>
                            </div>
                            <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                                <span class="text-[10px] text-gray-500 font-bold block mb-1 uppercase">Projected Growth</span>
                                <p id="frProjected" class="text-xs text-gray-300 leading-relaxed"></p>
                            </div>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 space-y-3">
                            <span class="text-[11px] font-bold text-white uppercase tracking-wider block border-b border-gray-800 pb-1.5">SEC Regulatory Filings Structure</span>
                            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                                <div>
                                    <strong class="text-blue-400 block mb-1">Form 10-K (Annual Report):</strong>
                                    <p id="sec10k" class="text-gray-400 text-[11px] leading-relaxed"></p>
                                </div>
                                <div>
                                    <strong class="text-blue-400 block mb-1">Form 10-Q (Quarterly Report):</strong>
                                    <p id="sec10q" class="text-gray-400 text-[11px] leading-relaxed"></p>
                                </div>
                                <div>
                                    <strong class="text-blue-400 block mb-1">Form 8-K (Material Events):</strong>
                                    <p id="sec8k" class="text-gray-400 text-[11px] leading-relaxed"></p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- VALUATION & TARGETS -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                        <i data-lucide="trending-up" class="w-4 h-4"></i> Valuation & Targets
                    </h3>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-gray-500 font-bold block uppercase">P/E Ratio</span>
                            <span id="targetPERatio" class="text-base font-mono font-bold text-white"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-emerald-400 font-bold block uppercase">Bull Target 12M</span>
                            <span id="targetBullPrice" class="text-base font-mono font-bold text-emerald-400"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-amber-400 font-bold block uppercase">Base Target 12M</span>
                            <span id="targetBasePrice" class="text-base font-mono font-bold text-amber-400"></span>
                        </div>
                        <div class="bg-[#0B0E14] p-3 rounded-xl border border-gray-900 text-center">
                            <span class="text-[10px] text-red-400 font-bold block uppercase">Bear Target 12M</span>
                            <span id="targetBearPrice" class="text-base font-mono font-bold text-red-400"></span>
                        </div>
                    </div>
                    <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 space-y-2.5">
                        <span class="text-[10px] font-bold text-gray-400 uppercase tracking-wider block">Price Targets — Proyecciones 12M en Porcentaje</span>
                        <div class="flex justify-between items-center text-xs border-b border-gray-900 pb-1.5">
                            <span class="flex items-center gap-1.5"><span class="w-2 h-2 bg-emerald-500 rounded-full"></span> 🟢 BULL:</span>
                            <span id="pctBullSpan" class="font-mono font-bold text-emerald-400"></span>
                        </div>
                        <div class="flex justify-between items-center text-xs border-b border-gray-900 pb-1.5">
                            <span class="flex items-center gap-1.5"><span class="w-2 h-2 bg-amber-500 rounded-full"></span> 🟡 BASE:</span>
                            <span id="pctBaseSpan" class="font-mono font-bold text-amber-400"></span>
                        </div>
                        <div class="flex justify-between items-center text-xs">
                            <span class="flex items-center gap-1.5"><span class="w-2 h-2 bg-red-500 rounded-full"></span> 🔴 BEAR:</span>
                            <span id="pctBearSpan" class="font-mono font-bold text-red-400"></span>
                        </div>
                    </div>
                </div>

                <!-- VS COMPETITORS -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                        <i data-lucide="swords" class="w-4 h-4"></i> vs Competitors
                    </h3>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-[10px] text-gray-500 font-bold block uppercase mb-1">Competitive Position (Moat)</span>
                            <p id="compPosicion" class="text-gray-300 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-[10px] text-gray-500 font-bold block uppercase mb-1">Main Competitors</span>
                            <p id="compCompetidores" class="text-gray-300 leading-relaxed"></p>
                        </div>
                    </div>
                    <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 mt-3">
                        <span class="text-[10px] text-gray-500 font-bold block uppercase mb-1">Why Better or Worse Than Peers?</span>
                        <p id="compBetterWorse" class="text-xs text-gray-300 leading-relaxed"></p>
                    </div>
                </div>

                <!-- QUICK SUMMARY + BUY NOW + AI THESIS -->
                <div>
                    <h3 class="text-xs font-extrabold text-blue-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                        <i data-lucide="brain" class="w-4 h-4"></i> AI Investment Thesis & Recommendation
                    </h3>
                    <div class="space-y-3">
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-amber-400 block mb-2">💡 In Simple Terms — ¿Por qué es buena inversión?</span>
                            <p id="bottomInSimpleTerms" class="text-xs text-gray-300 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-emerald-400 block mb-2">💰 Should You Buy at Current Price?</span>
                            <p id="bottomShouldBuy" class="text-xs text-gray-300 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-white block mb-2">📋 Recommendation & Reasoning</span>
                            <p id="recAndReasoningText" class="text-xs text-gray-300 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-blue-400 block mb-2">🧠 Full AI Investment Thesis</span>
                            <p id="thesisCore" class="text-xs text-gray-400 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-red-400 block mb-2">⚠️ Risks & Threats</span>
                            <p id="thesisRisks" class="text-xs text-gray-400 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-amber-400 block mb-2">📊 Wall Street Analysts Consensus</span>
                            <p id="thesisWS" class="text-xs text-gray-400 leading-relaxed"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900">
                            <span class="text-xs font-bold text-blue-400 block mb-2">🔢 AI Numeric Calculations & Growth</span>
                            <p id="thesisCalculations" class="text-xs text-gray-400 leading-relaxed font-mono text-[11px]"></p>
                        </div>
                        <div class="bg-[#0B0E14] p-4 rounded-xl border border-gray-900 border-l-2 border-l-blue-500">
                            <span class="text-xs font-bold text-white block mb-1">🏁 The Bottom Line</span>
                            <p id="theBottomLine" class="text-sm text-white leading-relaxed font-medium"></p>
                        </div>
                    </div>
                </div>

            </div>
            <!-- End tabs -->
        </div>

    </main>

    <script>
    // ============================================================
    // STATE
    // ============================================================
    let currentAnalysis = null;
    let currentTicker = null;
    let currentPrice = null;
    let qtChartInstance = null;
    let frChartInstance = null;
    let currentTimeframe = '7d';
    let debounceTimer = null;
    const API_BASE = "http://localhost:8000";

    // ============================================================
    // NAVIGATION
    // ============================================================
    function switchView(viewId) {
        document.querySelectorAll('.view-section').forEach(el => el.classList.add('hidden'));
        document.getElementById(viewId)?.classList.remove('hidden');
        lucide.createIcons();
        if (viewId === 'reportsView') renderFullReportsList();
        if (viewId === 'homeView') renderRecentReports();
    }

    function switchAnalysisTab(tabId, btn) {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.analysis-tab').forEach(el => el.classList.remove('active'));
        document.getElementById(tabId)?.classList.add('active');
        btn.classList.add('active');
        lucide.createIcons();

        // Cargar noticias cuando se abre la tab Notices
        if (tabId === 'noticesTab' && currentTicker) {
            loadNotices(currentTicker);
        }
        // Renderizar chart de Full Research
        if (tabId === 'fullResearchTab' && currentAnalysis) {
            fetchAndRenderFrChart(currentTimeframe);
        }
    }

    // ============================================================
    // QUOTE FETCH (debounced)
    // ============================================================
    function debounceQuoteFetch() {
        clearTimeout(debounceTimer);
        const val = document.getElementById('tickerInput').value.trim();
        if (val.length < 1) {
            document.getElementById('previewCard').classList.add('hidden');
            return;
        }
        debounceTimer = setTimeout(() => fetchQuote(val), 500);
    }

    async function fetchQuote(ticker) {
        try {
            const res = await fetch(`${API_BASE}/api/quote?ticker=${ticker}`);
            if (!res.ok) { document.getElementById('previewCard').classList.add('hidden'); return; }
            const d = await res.json();
            document.getElementById('previewLogo').src = d.logo_url;
            document.getElementById('previewTicker').innerText = d.ticker;
            document.getElementById('previewName').innerText = d.nombre_completo;
            document.getElementById('previewPrice').innerText = `$${d.precio}`;
            const pctEl = document.getElementById('previewPct');
            pctEl.innerText = `${d.cambio_pct > 0 ? '+' : ''}${d.cambio_pct}%`;
            pctEl.className = `text-xs font-bold px-2 py-0.5 rounded-md ${d.cambio_pct >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`;
            document.getElementById('mVolume').innerText = d.volumen;
            document.getElementById('mVwap').innerText = `$${d.vwap}`;
            document.getElementById('mHigh').innerText = `$${d.high}`;
            document.getElementById('mLow').innerText = `$${d.low}`;
            document.getElementById('previewCard').classList.remove('hidden');
            lucide.createIcons();
        } catch(e) {
            document.getElementById('previewCard').classList.add('hidden');
        }
    }

    // ============================================================
    // MAIN ANALYSIS
    // ============================================================
    async function ejecutarAnalisisAPI() {
        const ticker = document.getElementById('tickerInput').value.trim().toUpperCase();
        if (!ticker) return;

        const loading = document.getElementById('loadingScreen');
        const texts = [
            "Reviewing market conditions...",
            "Accessing SEC filings...",
            "Running valuation models...",
            "Generating AI thesis...",
            "Building multi-timeframe projections..."
        ];
        let ti = 0;
        loading.classList.remove('hidden');
        const interval = setInterval(() => {
            document.getElementById('loadingText').innerText = texts[ti % texts.length];
            ti++;
        }, 1800);

        try {
            const res = await fetch(`${API_BASE}/api/analyze?ticker=${ticker}`);
            if (!res.ok) throw new Error("API error");
            const data = await res.json();

            clearInterval(interval);
            loading.classList.add('hidden');

            currentAnalysis = data;
            currentTicker = data.ticker;
            currentPrice = data.precio_actual;
            currentTimeframe = '7d';

            renderDashboard(data);
            saveToHistory(data);
            switchView('dashboardView');
            // Default: mostrar Quick Take tab
            switchAnalysisTab('quickTakeTab', document.querySelector('.analysis-tab'));
        } catch(err) {
            clearInterval(interval);
            loading.classList.add('hidden');
            alert("Error al analizar el ticker. Verifica que el backend esté corriendo.");
        }
    }

    // ============================================================
    // RENDER DASHBOARD
    // ============================================================
    function renderDashboard(data) {
        const a = data.analisis;

        // Header
        document.getElementById('dashLogo').src = data.logo_url;
        document.getElementById('dashHeaderTitle').innerText = `${data.ticker} — ${data.nombre_completo}`;
        document.getElementById('dashAnalysisTime').innerText = `Analysis generated: ${data.fecha_analisis}`;
        document.getElementById('dashPrice').innerText = `$${data.precio_actual}`;

        // ---- QUICK TAKE TAB ----
        document.getElementById('qtBiggestPro').innerText = a.biggest_pro;
        document.getElementById('qtBiggestRisk').innerText = a.biggest_risk;
        document.getElementById('qtWatchFor').innerText = a.watch_for;
        document.getElementById('qtCompanySummary').innerText = a.company_summary_simple;

        // Chart Quick Take (1M data)
        renderQtChart(data.historial_fechas, data.historial_precios);

        // Targets 12M en Quick Take
        const price = data.precio_actual;
        document.getElementById('qt_bull_12m').innerText = `$${a.target_bull_12m.toFixed(2)}`;
        document.getElementById('qt_base_12m').innerText = `$${a.target_base_12m.toFixed(2)}`;
        document.getElementById('qt_bear_12m').innerText = `$${a.target_bear_12m.toFixed(2)}`;
        document.getElementById('qt_bull_12m_pct').innerText = pctDiff(price, a.target_bull_12m);
        document.getElementById('qt_base_12m_pct').innerText = pctDiff(price, a.target_base_12m);
        document.getElementById('qt_bear_12m_pct').innerText = pctDiff(price, a.target_bear_12m);

        // ---- FULL RESEARCH TAB ----
        // Verdict
        document.getElementById('vFairValue').innerText = `$${a.fair_value.toFixed(2)}`;
        document.getElementById('vUpside').innerText = `${a.upside_pct.toFixed(1)}%`;
        document.getElementById('vCurrentPrice').innerText = `$${price}`;
        document.getElementById('vRecommendation').innerText = a.recommendation;

        // Conviction
        const cscore = a.conviccion_score;
        document.getElementById('convictionScoreBadge').innerText = `${cscore}/100`;
        document.getElementById('convictionBar').style.width = `${cscore}%`;
        document.getElementById('convictionReason').innerText = a.conviccion_porque;

        // Full Research detail
        document.getElementById('frFinancialSummary').innerText = a.analisis_numeros_actuales;
        document.getElementById('frYoY').innerText = a.crecimiento_yoy;
        document.getElementById('frProjected').innerText = a.crecimiento_proyectado;
        document.getElementById('sec10k').innerText = a.sec_filing_10k;
        document.getElementById('sec10q').innerText = a.sec_filing_10q;
        document.getElementById('sec8k').innerText = a.sec_filing_8k;

        // Valuation 12M
        document.getElementById('targetPERatio').innerText = data.pe_ratio || "N/A";
        document.getElementById('targetBullPrice').innerText = `$${a.target_bull_12m.toFixed(2)}`;
        document.getElementById('targetBasePrice').innerText = `$${a.target_base_12m.toFixed(2)}`;
        document.getElementById('targetBearPrice').innerText = `$${a.target_bear_12m.toFixed(2)}`;
        document.getElementById('pctBullSpan').innerText = pctDiff(price, a.target_bull_12m);
        document.getElementById('pctBaseSpan').innerText = pctDiff(price, a.target_base_12m);
        document.getElementById('pctBearSpan').innerText = pctDiff(price, a.target_bear_12m);

        // Competitors
        document.getElementById('compPosicion').innerText = a.posicion_competitiva;
        document.getElementById('compCompetidores').innerText = a.principales_competidores;
        document.getElementById('compBetterWorse').innerText = a.porque_mejor_peor_inversion;

        // AI Thesis
        document.getElementById('bottomInSimpleTerms').innerText = a.in_simple_terms;
        document.getElementById('bottomShouldBuy').innerText = a.should_you_buy_now;
        document.getElementById('recAndReasoningText').innerText = a.recomendacion_porque;
        document.getElementById('thesisCore').innerText = a.tesis_inversion_completa;
        document.getElementById('thesisRisks').innerText = a.tesis_riesgos;
        document.getElementById('thesisWS').innerText = a.analistas_consenso;
        document.getElementById('thesisCalculations').innerText = a.calculos_y_crecimiento_ai;
        document.getElementById('theBottomLine').innerText = a.the_bottom_line;

        lucide.createIcons();
    }

    // ============================================================
    // CHART: QUICK TAKE (static 1M)
    // ============================================================
    function renderQtChart(labels, prices) {
        if (qtChartInstance) qtChartInstance.destroy();
        const ctx = document.getElementById('qtChart')?.getContext('2d');
        if (!ctx) return;
        qtChartInstance = buildChart(ctx, labels, prices, '#3b82f6');
    }

    // ============================================================
    // CHART: FULL RESEARCH (dynamic timeframe)
    // ============================================================
    async function fetchAndRenderFrChart(period) {
        if (!currentTicker) return;
        try {
            const res = await fetch(`${API_BASE}/api/history?ticker=${currentTicker}&period=${period}`);
            if (!res.ok) return;
            const d = await res.json();
            renderFrChart(d.fechas, d.precios, period);
            updateFrTargets(period);
        } catch(e) {
            console.error("Chart error", e);
        }
    }

    function renderFrChart(labels, prices, period) {
        if (frChartInstance) frChartInstance.destroy();
        const ctx = document.getElementById('frChart')?.getContext('2d');
        if (!ctx) return;
        frChartInstance = buildChart(ctx, labels, prices, '#6366f1');
    }

    function updateFrTargets(period) {
        if (!currentAnalysis) return;
        const a = currentAnalysis.analisis;
        const price = currentPrice;

        const map = {
            '7d':  { bull: a.target_bull_7d, base: a.target_base_7d, bear: a.target_bear_7d, label: '7 Days' },
            '1mo': { bull: a.target_bull_30d, base: a.target_base_30d, bear: a.target_bear_30d, label: '30 Days' },
            '3mo': { bull: a.target_bull_3m, base: a.target_base_3m, bear: a.target_bear_3m, label: '3 Months' },
            '6mo': { bull: a.target_bull_6m, base: a.target_base_6m, bear: a.target_bear_6m, label: '6 Months' },
            '1y':  { bull: a.target_bull_12m, base: a.target_base_12m, bear: a.target_bear_12m, label: '12 Months' },
        };

        const t = map[period] || map['1y'];
        document.getElementById('frBullLabel').innerText = `🟢 Bull ${t.label}`;
        document.getElementById('frBaseLabel').innerText = `🟡 Base ${t.label}`;
        document.getElementById('frBearLabel').innerText = `🔴 Bear ${t.label}`;
        document.getElementById('fr_bull').innerText = `$${(t.bull || 0).toFixed(2)}`;
        document.getElementById('fr_base').innerText = `$${(t.base || 0).toFixed(2)}`;
        document.getElementById('fr_bear').innerText = `$${(t.bear || 0).toFixed(2)}`;
        document.getElementById('fr_bull_pct').innerText = pctDiff(price, t.bull);
        document.getElementById('fr_base_pct').innerText = pctDiff(price, t.base);
        document.getElementById('fr_bear_pct').innerText = pctDiff(price, t.bear);
    }

    function changeTimeframe(period, btn) {
        document.querySelectorAll('.tf-pill').forEach(el => el.classList.remove('active'));
        btn.classList.add('active');
        currentTimeframe = period;
        fetchAndRenderFrChart(period);
    }

    // ============================================================
    // CHART BUILDER (reusable)
    // ============================================================
    function buildChart(ctx, labels, prices, color) {
        const gradient = ctx.createLinearGradient(0, 0, 0, 256);
        gradient.addColorStop(0, color + '33');
        gradient.addColorStop(1, color + '00');
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    data: prices,
                    borderColor: color,
                    borderWidth: 2,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: {
                    mode: 'index', intersect: false,
                    callbacks: { label: ctx => `$${ctx.parsed.y.toFixed(2)}` }
                }},
                scales: {
                    x: { grid: { color: '#1F2532' }, ticks: { color: '#6b7280', font: { size: 10 }, maxTicksLimit: 8 } },
                    y: { grid: { color: '#1F2532' }, ticks: { color: '#6b7280', font: { size: 10 }, callback: v => `$${v}` } }
                }
            }
        });
    }

    // ============================================================
    // NOTICES (Yahoo Finance – últimos 6 meses)
    // ============================================================
    async function loadNotices(ticker) {
        const container = document.getElementById('noticesNewsContainer');
        container.innerHTML = `<div class="text-center text-gray-500 text-sm py-8">
            <p class="animate-pulse">Loading news feed...</p></div>`;
        try {
            const res = await fetch(`${API_BASE}/api/news?ticker=${ticker}`);
            if (!res.ok) throw new Error();
            const d = await res.json();
            document.getElementById('newsCount').innerText = `${d.total} articles found`;
            if (!d.noticias || d.noticias.length === 0) {
                container.innerHTML = `<p class="text-gray-500 text-sm text-center py-8">No recent news found for ${ticker}.</p>`;
                return;
            }
            container.innerHTML = d.noticias.map(n => `
                <div class="news-card">
                    <div class="flex items-start justify-between gap-3 mb-1">
                        <p class="text-xs font-semibold text-white leading-snug flex-1">${n.title}</p>
                    </div>
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-[10px] font-bold text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded">${n.publisher}</span>
                        <span class="text-[10px] text-gray-500">${n.publish_time}</span>
                    </div>
                    <p class="text-[11px] text-gray-400 leading-relaxed">${n.summary || 'No summary available.'}</p>
                    ${n.link ? `<a href="${n.link}" target="_blank" class="text-[10px] text-blue-500 hover:text-blue-400 mt-1 inline-block">Read full article →</a>` : ''}
                </div>
            `).join('');
        } catch(e) {
            container.innerHTML = `<p class="text-red-400 text-xs py-4">Error loading news. Check API connection.</p>`;
        }
    }

    // ============================================================
    // HISTORY (localStorage)
    // ============================================================
    function saveToHistory(data) {
        let history = JSON.parse(localStorage.getItem('vertex_history') || '[]');
        history = history.filter(h => h.ticker !== data.ticker);
        history.unshift({
            ticker: data.ticker,
            nombre: data.nombre_completo,
            precio: data.precio_actual,
            logo: data.logo_url,
            recomendacion: data.analisis.recommendation,
            fecha: data.fecha_analisis,
            conviction: data.analisis.conviccion_score,
            upside: data.analisis.upside_pct
        });
        if (history.length > 20) history = history.slice(0, 20);
        localStorage.setItem('vertex_history', JSON.stringify(history));
        renderRecentReports();
    }

    function renderRecentReports() {
        const history = JSON.parse(localStorage.getItem('vertex_history') || '[]');
        const el = document.getElementById('homeRecentList');
        if (!el) return;
        if (history.length === 0) {
            el.innerHTML = `<p class="text-gray-600 text-xs text-center py-4">No reports yet. Run your first analysis above.</p>`;
            return;
        }
        el.innerHTML = history.slice(0, 4).map(h => reportCard(h)).join('');
        lucide.createIcons();
    }

    function renderFullReportsList() {
        const history = JSON.parse(localStorage.getItem('vertex_history') || '[]');
        const el = document.getElementById('fullReportsList');
        if (!el) return;
        if (history.length === 0) {
            el.innerHTML = `<p class="text-gray-600 text-xs text-center py-8">No reports yet.</p>`;
            return;
        }
        el.innerHTML = history.map(h => reportCard(h)).join('');
        lucide.createIcons();
    }

    function reportCard(h) {
        const recColor = h.recomendacion === 'BUY' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                       : h.recomendacion === 'SELL' || h.recomendacion === 'AVOID' ? 'text-red-400 bg-red-500/10 border-red-500/20'
                       : 'text-amber-400 bg-amber-500/10 border-amber-500/20';
        return `
        <div class="bg-[#0B0E14] border border-gray-900 rounded-xl p-4 flex items-center justify-between hover:border-blue-500/30 transition cursor-pointer">
            <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-lg bg-[#11141D] border border-gray-800 overflow-hidden flex-shrink-0">
                    <img src="${h.logo}" class="w-full h-full object-contain" onerror="this.src='https://ui-avatars.com/api/?name=${h.ticker}&background=0B0E14&color=3b82f6&font-size=0.4&bold=true'">
                </div>
                <div>
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-bold text-white">${h.ticker}</span>
                        <span class="text-xs px-1.5 py-0.5 rounded border font-bold ${recColor}">${h.recomendacion}</span>
                    </div>
                    <p class="text-[10px] text-gray-500">${h.nombre} · ${h.fecha}</p>
                </div>
            </div>
            <div class="text-right">
                <p class="text-sm font-mono font-bold text-white">$${h.precio}</p>
                <p class="text-[10px] text-blue-400">+${(h.upside || 0).toFixed(1)}% upside</p>
            </div>
        </div>`;
    }

    // ============================================================
    // UTILS
    // ============================================================
    function pctDiff(current, target) {
        if (!current || !target) return '';
        const diff = ((target - current) / current) * 100;
        return `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}%`;
    }

    // ============================================================
    // INIT
    // ============================================================
    document.addEventListener('DOMContentLoaded', () => {
        lucide.createIcons();
        renderRecentReports();
    });
    </script>
</body>
</html>