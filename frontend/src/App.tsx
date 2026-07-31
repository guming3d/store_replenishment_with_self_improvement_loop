import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { Theme } from '@radix-ui/themes';
import { ToastContainer, Zoom } from 'react-toastify';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import {
  IconBook,
  IconLayoutDashboard,
  IconReportSearch,
  IconSettings,
  IconHistory,
  IconMenu2,
  IconBolt,
  IconRobot,
  IconWorld,
  IconBuildingStore,
  IconRefresh,
  IconLogout,
  IconUser,
  IconLock,
  IconCircleCheck,
  IconSun,
  IconMoon,
  IconDeviceDesktop,
  IconShieldLock,
  IconGauge,
  IconListCheck,
  IconBrain,
} from '@tabler/icons-react';
import { Alert, Badge, Button, Input, Segmented, Select, Space, Spin, Tag, Tooltip, Typography } from './components/ui';
import { message } from './components/ui';
import {
  clearAuthToken,
  fetchAgentStatus,
  fetchAttributionReviewCount,
  fetchCurrentUser,
  fetchShops,
  fetchSkus,
  hasAuthToken,
  isAuthError,
  login,
  subscribeMockMode,
} from './api';
import brandLogoEn from './assets/branding/store-replenishment-en.png';
import brandLogoZh from './assets/branding/store-replenishment-zh.png';
import { useI18n } from './i18n';
import { useAppStore } from './lib/store/appStore';
import ExceptionCockpit from './pages/ExceptionCockpit';
import Explainability from './pages/Explainability';
import Parameters from './pages/Parameters';
import RunHistory from './pages/RunHistory';
import ScenarioRouting from './pages/ScenarioRouting';
import Suggestions from './pages/Suggestions';
import AttributionCases from './pages/AttributionCases';
import AttributionCaseDetailPage from './pages/AttributionCaseDetail';
import AdminOverviewPage from './pages/AdminOverview';
import AdminJobsPage from './pages/AdminJobs';
import AdminReviewQueuePage from './pages/AdminReviewQueue';
import AdminKnowledgePage from './pages/AdminKnowledge';
import Guide from './pages/Guide';
import type { AgentStatus, CurrentUser, ReplenishmentResult, Shop, Sku } from './types';

const { Text, Title } = Typography;

const BRAND_LOGOS = {
  en: { src: brandLogoEn, width: 470, height: 254 },
  zh: { src: brandLogoZh, width: 468, height: 248 },
} as const;

interface StoreContextValue {
  shops: Shop[];
  skus: Sku[];
  selectedShop?: string;
  selectedSku?: string;
  setSelectedShop: (shop?: string) => void;
  setSelectedSku: (sku?: string) => void;
  batchResults: ReplenishmentResult[];
  setBatchResults: (results: ReplenishmentResult[]) => void;
  mockMode: boolean;
  engineMode: 'algo' | 'agent';
  setEngineMode: (mode: 'algo' | 'agent') => void;
  agentStatus?: AgentStatus;
}

const StoreContext = createContext<StoreContextValue | null>(null);

export const useStoreContext = () => {
  const context = useContext(StoreContext);
  if (!context) throw new Error('useStoreContext must be used within StoreContext.Provider');
  return context;
};

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  badge?: number;
}

/** Client-side convenience only; the API enforces the role on every `/api/admin/` request. */
function RequireAdmin({ user, children }: { user?: CurrentUser; children: React.ReactNode }) {
  if (!user) return <Spin spinning />;
  if (user.role !== 'admin') return <Navigate to="/suggestions" replace />;
  return <>{children}</>;
}

export default function App() {
  const location = useLocation();
  const { t, lang, setLang } = useI18n();
  const brandLogo = BRAND_LOGOS[lang];

  const themeMode = useAppStore((s) => s.theme);
  const setThemeMode = useAppStore((s) => s.setTheme);
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);

  const [activeTheme, setActiveTheme] = useState<'light' | 'dark'>('light');
  const [authenticated, setAuthenticated] = useState(hasAuthToken);
  const [loginFailed, setLoginFailed] = useState(false);
  const [loginSubmitting, setLoginSubmitting] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginErrors, setLoginErrors] = useState<{ username?: string; password?: string }>({});
  const [shops, setShops] = useState<Shop[]>([]);
  const [skus, setSkus] = useState<Sku[]>([]);
  const [selectedShop, setSelectedShop] = useState<string>();
  const [selectedSku, setSelectedSku] = useState<string>();
  const [batchResults, setBatchResults] = useState<ReplenishmentResult[]>([]);
  const [mockMode, setMockMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [engineMode, setEngineMode] = useState<'algo' | 'agent'>('algo');
  const [agentStatus, setAgentStatus] = useState<AgentStatus>();
  const [masterDataError, setMasterDataError] = useState<string>();
  const [attributionReviewCount, setAttributionReviewCount] = useState(0);
  const [currentUser, setCurrentUser] = useState<CurrentUser>();
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [compactNavigation, setCompactNavigation] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 991px)').matches,
  );

  useEffect(() => {
    const compute = (): 'light' | 'dark' =>
      themeMode === 'system'
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : themeMode;
    setActiveTheme(compute());
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => { if (themeMode === 'system') setActiveTheme(compute()); };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [themeMode]);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 991px)');
    const handler = (event: MediaQueryListEvent) => {
      setCompactNavigation(event.matches);
      if (!event.matches) setMobileNavigationOpen(false);
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const loadMasterData = async () => {
    setLoading(true);
    setMasterDataError(undefined);
    try {
      const [shopData, skuData] = await Promise.all([fetchShops(), fetchSkus()]);
      setShops(shopData);
      setSkus(skuData);
      setSelectedShop((current) => current ?? shopData[0]?.shop_code);
    } catch (error) {
      if (isAuthError(error)) {
        clearAuthToken();
        setAuthenticated(false);
        setLoginFailed(true);
        return;
      }
      setShops([]);
      setSkus([]);
      setSelectedShop(undefined);
      const detail = error instanceof Error ? error.message : t('app.loadFailed');
      setMasterDataError(detail);
      message.error(t('app.loadFailed'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const unsubscribe = subscribeMockMode(setMockMode);
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    void loadMasterData();
    void fetchAgentStatus().then(setAgentStatus).catch(() => setAgentStatus(undefined));
    void fetchCurrentUser().then(setCurrentUser).catch(() => setCurrentUser(undefined));
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated) return undefined;
    const refreshReviewCount = () => {
      void fetchAttributionReviewCount()
        .then((result) => setAttributionReviewCount(result.needs_review))
        .catch((error) => console.warn('Failed to refresh attribution review count.', error));
    };
    refreshReviewCount();
    const timer = window.setInterval(refreshReviewCount, 30000);
    return () => window.clearInterval(timer);
  }, [authenticated, location.pathname]);

  const handleLogin = async () => {
    const errors: { username?: string; password?: string } = {};
    if (!username.trim()) errors.username = t('auth.requiredUser');
    if (!password) errors.password = t('auth.requiredPassword');
    setLoginErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setLoginSubmitting(true);
    try {
      await login(username.trim(), password);
      setLoginFailed(false);
      setAuthenticated(true);
    } catch {
      clearAuthToken();
      setLoginFailed(true);
      setAuthenticated(false);
    } finally {
      setLoginSubmitting(false);
    }
  };

  const handleLogout = () => {
    clearAuthToken();
    setAuthenticated(false);
    setLoginFailed(false);
    setAgentStatus(undefined);
    setBatchResults([]);
    setSelectedSku(undefined);
    setMasterDataError(undefined);
    setAttributionReviewCount(0);
    setCurrentUser(undefined);
    setLoading(false);
  };

  const contextValue = useMemo<StoreContextValue>(
    () => ({ shops, skus, selectedShop, selectedSku, setSelectedShop, setSelectedSku, batchResults, setBatchResults, mockMode, engineMode, setEngineMode, agentStatus }),
    [shops, skus, selectedShop, selectedSku, batchResults, mockMode, engineMode, agentStatus],
  );

  const menuItems = useMemo<NavItem[]>(
    () => [
      { key: '/guide', icon: <IconBook size={20} />, label: t('menu.guide') },
      { key: '/suggestions', icon: <IconLayoutDashboard size={20} />, label: t('menu.suggestions') },
      { key: '/attribution', icon: <IconReportSearch size={20} />, label: t('menu.attribution'), badge: attributionReviewCount },
      { key: '/parameters', icon: <IconSettings size={20} />, label: t('menu.parameters') },
      { key: '/history', icon: <IconHistory size={20} />, label: t('menu.history') },
    ],
    [t, attributionReviewCount],
  );

  const isAdmin = currentUser?.role === 'admin';
  const isAdminRoute = location.pathname.startsWith('/admin');

  const adminMenuItems = useMemo<NavItem[]>(
    () => [
      { key: '/admin', icon: <IconGauge size={20} />, label: t('menu.adminOverview') },
      { key: '/admin/jobs', icon: <IconListCheck size={20} />, label: t('menu.adminJobs') },
      { key: '/admin/review-queue', icon: <IconShieldLock size={20} />, label: t('menu.adminQueue'), badge: attributionReviewCount },
      { key: '/admin/knowledge', icon: <IconBrain size={20} />, label: t('menu.adminKnowledge') },
    ],
    [t, attributionReviewCount],
  );

  const activeKey =
    location.pathname === '/'
      ? '/suggestions'
      : location.pathname.startsWith('/attribution')
        ? '/attribution'
        : location.pathname;

  const cycleTheme = () => {
    const next = themeMode === 'light' ? 'dark' : themeMode === 'dark' ? 'system' : 'light';
    setThemeMode(next);
  };
  const ThemeIcon = themeMode === 'dark' ? IconMoon : themeMode === 'light' ? IconSun : IconDeviceDesktop;

  const navToggleLabel = compactNavigation
    ? t(mobileNavigationOpen ? 'app.hideNavigation' : 'app.showNavigation')
    : t(sidebarCollapsed ? 'app.showNavigation' : 'app.hideNavigation');

  const handleNavToggle = () => {
    if (compactNavigation) setMobileNavigationOpen((current) => !current);
    else toggleSidebar();
  };

  const brandImg = (
    <img
      className="app-brand-logo"
      src={brandLogo.src}
      width={brandLogo.width}
      height={brandLogo.height}
      alt={t('app.brandTitle')}
    />
  );

  const renderNavItem = (item: NavItem, collapsed: boolean) => (
    <NavLink
      key={item.key}
      to={item.key}
      end={item.key === '/admin'}
      className={`app-nav-item ${activeKey === item.key ? 'app-nav-item-active' : ''} ${collapsed ? 'app-nav-item-collapsed' : ''}`}
      onClick={() => setMobileNavigationOpen(false)}
      title={collapsed ? item.label : undefined}
    >
      <span className="app-nav-icon">{item.icon}</span>
      {!collapsed && <span className="app-nav-text">{item.label}</span>}
      {!collapsed && item.badge ? <Badge count={item.badge} size="small" /> : null}
    </NavLink>
  );

  const renderNav = (collapsed: boolean) => (
    <nav className="app-nav" aria-label={t('app.nav')}>
      <div className="nav-label">{!collapsed && t('app.nav')}</div>
      {menuItems.map((item) => renderNavItem(item, collapsed))}
      {isAdmin && (
        <>
          <div className="nav-label nav-label-admin">{!collapsed && t('app.navAdmin')}</div>
          {adminMenuItems.map((item) => renderNavItem(item, collapsed))}
        </>
      )}
    </nav>
  );

  const langSelect = (
    <Select
      className="lang-select"
      size="small"
      value={lang}
      suffixIcon={<IconWorld size={16} />}
      onChange={(value) => value && setLang(value as 'zh' | 'en')}
      options={[
        { value: 'zh', label: '中文' },
        { value: 'en', label: 'English' },
      ]}
    />
  );

  return (
    <Theme appearance={activeTheme} accentColor="indigo" grayColor="slate" radius="large" scaling="100%">
      {authenticated ? (
        <StoreContext.Provider value={contextValue}>
          <a className="skip-link" href="#main-content">{t('app.skipToContent')}</a>
          <div className={`app-shell ${isAdminRoute ? 'app-shell-admin' : ''}`}>
            <header className="app-header">
              <div className="app-header-leading">
                <Tooltip title={navToggleLabel} placement="bottom">
                  <Button
                    type="text"
                    className="navigation-toggle"
                    icon={<IconMenu2 size={20} />}
                    aria-label={navToggleLabel}
                    onClick={handleNavToggle}
                  />
                </Tooltip>
                <div className="app-header-brand">{brandImg}</div>
                {isAdminRoute && (
                  <Tag className="app-admin-chip" color="processing">
                    <IconShieldLock size={13} /> {t('app.adminMode')}
                  </Tag>
                )}
              </div>
              <div className="app-header-controls">
                <Space size="small" className="app-header-switches">
                  {!isAdminRoute && (
                    <Segmented
                    className="engine-select"
                    size="small"
                    value={engineMode}
                    onChange={(val) => setEngineMode(val as 'algo' | 'agent')}
                    options={[
                      {
                        value: 'algo',
                        label: (
                          <Tooltip title={t('engine.tipAlgo')}>
                            <span className="engine-option"><IconBolt size={15} /> {t('engine.algo')}</span>
                          </Tooltip>
                        ),
                      },
                      {
                        value: 'agent',
                        disabled: !agentStatus?.available,
                        label: (
                          <Tooltip title={agentStatus?.available ? t('engine.tipAgent') : t('engine.tipUnavailable')}>
                            <span className="engine-option"><IconRobot size={15} /> {t('engine.agent')}</span>
                          </Tooltip>
                        ),
                      },
                    ]}
                  />
                  )}
                  {langSelect}
                </Space>
                <Space size="small" className="app-header-actions">
                  {!isAdminRoute && (
                    <>
                  <Select
                    className="selector"
                    size="small"
                    style={{ width: 'clamp(128px, 9vw, 142px)' }}
                    prefix={<IconBuildingStore size={16} />}
                    placeholder={t('app.selectShop')}
                    value={selectedShop}
                    loading={loading}
                    showSearch
                    options={shops.map((shop) => ({ value: shop.shop_code, label: `${shop.shop_name} · ${shop.city}` }))}
                    onChange={setSelectedShop}
                  />
                  <Select
                    className="selector wide"
                    size="small"
                    style={{ width: 'clamp(150px, 11vw, 176px)' }}
                    placeholder={t('app.selectSku')}
                    value={selectedSku}
                    loading={loading}
                    allowClear
                    showSearch
                    options={skus.map((sku) => ({ value: sku.goods_code, label: `${sku.goods_name} (${sku.goods_code})` }))}
                    onChange={setSelectedSku}
                  />
                  <Button size="small" icon={<IconRefresh size={15} />} onClick={() => void loadMasterData()}>
                    {t('app.refresh')}
                  </Button>
                    </>
                  )}
                  {currentUser && (
                    <Text className="app-user-chip" type="secondary">
                      <IconUser size={14} /> {currentUser.username}
                    </Text>
                  )}
                  <Tooltip title={`Theme: ${themeMode}`}>
                    <Button size="small" type="text" icon={<ThemeIcon size={17} />} aria-label="theme" onClick={cycleTheme} />
                  </Tooltip>
                  <Button size="small" icon={<IconLogout size={15} />} onClick={handleLogout}>
                    {t('auth.logout')}
                  </Button>
                </Space>
              </div>
            </header>

            <div className="app-body">
              {!compactNavigation && (
                <aside className={`app-sider ${sidebarCollapsed ? 'app-sider-collapsed' : ''}`}>
                  <div className="app-sider-scroll">{renderNav(sidebarCollapsed)}</div>
                  <div className="sider-footer">
                    <span className="sider-footer-dot" />
                    {!sidebarCollapsed && <Text className="sider-footer-text" type="secondary">{t('app.footer')}</Text>}
                  </div>
                </aside>
              )}

              {compactNavigation && mobileNavigationOpen && (
                <div className="mobile-nav-overlay" onClick={() => setMobileNavigationOpen(false)}>
                  <aside className="app-sider app-sider-mobile" onClick={(e) => e.stopPropagation()}>
                    <div className="app-sider-brand">{brandImg}</div>
                    <div className="app-sider-scroll">{renderNav(false)}</div>
                    <div className="sider-footer">
                      <span className="sider-footer-dot" />
                      <Text className="sider-footer-text" type="secondary">{t('app.footer')}</Text>
                    </div>
                  </aside>
                </div>
              )}

              <main id="main-content" tabIndex={-1} className="app-content">
                {masterDataError && (
                  <Alert
                    showIcon
                    type="error"
                    style={{ marginBottom: 16 }}
                    message={t('app.loadFailed')}
                    description={masterDataError}
                  />
                )}
                <Spin spinning={loading && !shops.length} tip={t('app.loading')}>
                  <Routes>
                    <Route path="/" element={<Navigate to="/suggestions" replace />} />
                    <Route path="/guide" element={<Guide />} />
                    <Route path="/suggestions" element={<Suggestions />} />
                    <Route path="/attribution" element={<AttributionCases />} />
                    <Route path="/attribution/:caseId" element={<AttributionCaseDetailPage />} />
                    <Route path="/parameters" element={<Parameters />} />
                    <Route path="/routing" element={<ScenarioRouting />} />
                    <Route path="/exceptions" element={<ExceptionCockpit />} />
                    <Route path="/explainability" element={<Explainability />} />
                    <Route path="/history" element={<RunHistory />} />
                    <Route path="/admin" element={<RequireAdmin user={currentUser}><AdminOverviewPage /></RequireAdmin>} />
                    <Route path="/admin/jobs" element={<RequireAdmin user={currentUser}><AdminJobsPage /></RequireAdmin>} />
                    <Route path="/admin/review-queue" element={<RequireAdmin user={currentUser}><AdminReviewQueuePage /></RequireAdmin>} />
                    <Route path="/admin/knowledge" element={<RequireAdmin user={currentUser}><AdminKnowledgePage /></RequireAdmin>} />
                  </Routes>
                </Spin>
              </main>
            </div>
          </div>
        </StoreContext.Provider>
      ) : (
        <div className="login-shell">
          <div className="login-card">
            <aside className="login-aside">
              <div className="login-aside-body">
                <div className="login-brand-plate">
                  <img
                    className="login-brand-logo"
                    src={brandLogo.src}
                    width={brandLogo.width}
                    height={brandLogo.height}
                    alt={t('app.brandTitle')}
                  />
                </div>
                <ul className="login-aside-points">
                  <li><IconCircleCheck size={16} /> {t('menu.suggestions')}</li>
                  <li><IconCircleCheck size={16} /> {t('menu.attribution')}</li>
                  <li><IconCircleCheck size={16} /> {t('menu.parameters')}</li>
                  <li><IconCircleCheck size={16} /> {t('menu.history')}</li>
                </ul>
              </div>
              <div className="login-aside-foot">{t('app.footer')}</div>
            </aside>

            <section className="login-panel">
              <img
                className="login-mobile-logo"
                src={brandLogo.src}
                width={brandLogo.width}
                height={brandLogo.height}
                alt={t('app.brandTitle')}
              />
              <div className="login-header">
                <div className="login-heading">
                  <Title level={4} className="login-title">{t('auth.title')}</Title>
                  <Text type="secondary">{t('auth.subtitle')}</Text>
                </div>
                {langSelect}
              </div>

              {loginFailed && (
                <Alert
                  showIcon
                  type="error"
                  style={{ marginTop: 16 }}
                  message={t('auth.invalid')}
                />
              )}

              <form
                className="login-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleLogin();
                }}
              >
                <div className="login-field">
                  <label htmlFor="login-username">{t('auth.username')}</label>
                  <Input
                    id="login-username"
                    size="large"
                    prefix={<IconUser size={16} />}
                    autoComplete="username"
                    value={username}
                    status={loginErrors.username ? 'error' : undefined}
                    onChange={(event) => {
                      setUsername(event.target.value);
                      setLoginFailed(false);
                      setLoginErrors((prev) => ({ ...prev, username: undefined }));
                    }}
                  />
                  {loginErrors.username && <div className="login-error">{loginErrors.username}</div>}
                </div>
                <div className="login-field">
                  <label htmlFor="login-password">{t('auth.password')}</label>
                  <Input.Password
                    id="login-password"
                    size="large"
                    prefix={<IconLock size={16} />}
                    autoComplete="current-password"
                    value={password}
                    status={loginErrors.password ? 'error' : undefined}
                    onChange={(event) => {
                      setPassword(event.target.value);
                      setLoginFailed(false);
                      setLoginErrors((prev) => ({ ...prev, password: undefined }));
                    }}
                  />
                  {loginErrors.password && <div className="login-error">{loginErrors.password}</div>}
                </div>
                <Button className="login-submit" type="primary" htmlType="submit" size="large" block loading={loginSubmitting}>
                  {t('auth.signIn')}
                </Button>
              </form>
            </section>
          </div>
        </div>
      )}
      <ToastContainer transition={Zoom} position="top-center" theme={activeTheme} hideProgressBar closeButton={false} />
    </Theme>
  );
}
