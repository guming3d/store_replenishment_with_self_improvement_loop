import axios, { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import {
  makeMockTrace,
  mockBatchResults,
  mockClearInventory,
  mockClearSkuConfig,
  mockClearStoreConfig,
  mockConfigSchema,
  mockConfigStatus,
  mockEffectiveConfig,
  mockExceptions,
  mockInventory,
  mockSaveInventory,
  mockSaveSkuConfig,
  mockSaveStoreConfig,
  mockShops,
  mockSkus,
  mockStoreSkuConfigs,
} from './mock';
import type {
  AdjustItem,
  AdjustResult,
  AgentStatus,
  AttributionCaseDetail,
  AttributionCaseFilters,
  AttributionCaseList,
  AttributionJob,
  AttributionOutputLanguage,
  AttributionReviewCount,
  AttributionReviewRequest,
  AdminJobList,
  AdminOverview,
  AdminReviewQueue,
  BulkDismissResult,
  ConfigSchema,
  ConfigStatus,
  CurrentUser,
  DiagnosticAgent,
  EffectiveConfig,
  ExceptionItem,
  InventoryFields,
  KnowledgeEntry,
  ReplenishmentParams,
  ReplenishmentResult,
  RunDetail,
  RunSubmissionReadiness,
  RunSubmissionResult,
  RunSummary,
  Shop,
  Sku,
  SkuBulkItem,
  SkuBulkResult,
  StoreInventory,
  StoreSkuConfig,
  TraceDetail,
  UserRole,
} from './types';

const rawApiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.trim();
const normalizedApiBase = rawApiBase ? rawApiBase.replace(/\/+$/, '') : '';

// Requests below are rooted at /api/...; normalize the base so values like
// "/api" or "https://host/api" do not become "/api/api/..." in deployed usage.
export const API_BASE = !normalizedApiBase || normalizedApiBase === '/api'
  ? ''
  : normalizedApiBase.endsWith('/api')
    ? normalizedApiBase.slice(0, -4)
    : normalizedApiBase;

const fallbackHosts = new Set(['localhost', '127.0.0.1']);
const allowMockFallback = import.meta.env.DEV
  || import.meta.env.VITE_ENABLE_MOCK_FALLBACK === 'true'
  || (typeof window !== 'undefined' && fallbackHosts.has(window.location.hostname));

// A stopped backend fails instantly with a connection error, so a short timeout was
// never what made mock fallback fast. It only ever fired when the server had accepted
// the request and was still working — a cold start (~20s), or a read queued behind the
// attribution worker's write transaction. In both cases aborting early is wrong: it
// reports "backend unavailable" for a backend that is about to answer. Use one
// generous timeout everywhere.
const CLIENT_TIMEOUT_MS = 30000;
const READ_RETRY_DELAY_MS = 1000;

const client = axios.create({
  baseURL: API_BASE,
  timeout: CLIENT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

// Agent orchestration runs an LLM call per SKU, so it needs a much longer timeout.
const agentClient = axios.create({
  baseURL: API_BASE,
  timeout: 180000,
  headers: { 'Content-Type': 'application/json' },
});

const AUTH_TOKEN_STORAGE_KEY = 'replenish.auth.token';

const readAuthToken = (): string | null => {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
};

let authToken = readAuthToken();

export const hasAuthToken = (): boolean => Boolean(authToken);

export const setAuthToken = (token: string | null) => {
  authToken = token;
  if (typeof window === 'undefined') return;
  try {
    if (token) {
      window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    }
  } catch {
    /* ignore storage access errors */
  }
};

export const clearAuthToken = () => setAuthToken(null);

const attachAuthHeader = (config: InternalAxiosRequestConfig): InternalAxiosRequestConfig => {
  if (!authToken) return config;
  const headers = config.headers instanceof AxiosHeaders ? config.headers : new AxiosHeaders(config.headers);
  headers.set('Authorization', `Bearer ${authToken}`);
  config.headers = headers;
  return config;
};

client.interceptors.request.use(attachAuthHeader);
agentClient.interceptors.request.use(attachAuthHeader);

let mockMode = false;
const listeners = new Set<(enabled: boolean) => void>();

const setMockMode = (enabled: boolean) => {
  if (mockMode === enabled) return;
  mockMode = enabled;
  listeners.forEach((listener) => listener(enabled));
};

export const subscribeMockMode = (listener: (enabled: boolean) => void) => {
  listeners.add(listener);
  listener(mockMode);
  return () => { listeners.delete(listener); };
};

const isRetryableReadError = (error: unknown): boolean => (
  axios.isAxiosError(error)
  && (error.code === 'ECONNABORTED' || error.response == null)
);

const sleep = (ms: number) => new Promise((resolve) => { window.setTimeout(resolve, ms); });

export const isAuthError = (error: unknown): boolean => (
  axios.isAxiosError(error) && error.response?.status === 401
);

export const apiErrorMessage = (error: unknown): string => {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : String(error);
  }
  const detail = error.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (!item || typeof item !== 'object') return String(item);
        const location = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
        const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      })
      .join('; ');
  }
  return error.message;
};

const shouldUseMockFallback = (error: unknown): boolean => (
  allowMockFallback && axios.isAxiosError(error) && !isAuthError(error)
);

async function runReadRequest<T>(request: () => Promise<T>, retry: boolean): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (!retry || !isRetryableReadError(error)) throw error;
    await sleep(READ_RETRY_DELAY_MS);
    return request();
  }
}

async function withFallback<T>(request: () => Promise<T>, fallback: () => T): Promise<T> {
  try {
    // A retry here would only delay the mock fallback that already covers this data.
    const data = await runReadRequest(request, !allowMockFallback);
    setMockMode(false);
    return data;
  } catch (error) {
    if (!allowMockFallback || isAuthError(error)) {
      setMockMode(false);
      throw error;
    }

    console.warn('Backend unavailable, using mock data.', error);
    setMockMode(true);
    return fallback();
}
}

// Attribution and administrator reads have no mock counterpart, so a transient failure
// has nothing to degrade into and must be retried in every environment.
async function withoutFallback<T>(request: () => Promise<T>): Promise<T> {
  const data = await runReadRequest(request, true);
  setMockMode(false);
  return data;
}

interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  role: UserRole;
  username: string;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await client.post<LoginResponse>('/api/auth/login', { username, password });
  setAuthToken(data.access_token);
  return data;
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return withoutFallback(async () => (await client.get<CurrentUser>('/api/auth/me')).data);
}

export async function fetchAdminOverview(): Promise<AdminOverview> {
  return withoutFallback(async () => (await client.get<AdminOverview>('/api/admin/overview')).data);
}

export async function fetchAdminJobs(page = 1, pageSize = 50): Promise<AdminJobList> {
  return withoutFallback(async () => (await client.get<AdminJobList>(
    '/api/admin/jobs', { params: { page, page_size: pageSize } })).data);
}

export async function fetchAdminReviewQueue(
  filters: { status?: string; page?: number; page_size?: number } = {},
): Promise<AdminReviewQueue> {
  return withoutFallback(async () => (await client.get<AdminReviewQueue>(
    '/api/admin/review-queue', { params: filters })).data);
}

export async function bulkDismissAttributionCases(
  cases: { case_id: string; expected_version: number }[],
  reason: string,
): Promise<BulkDismissResult> {
  const { data } = await client.post<BulkDismissResult>(
    '/api/admin/attribution/cases/bulk-dismiss',
    { cases, reason },
  );
  setMockMode(false);
  return data;
}

export async function fetchAttributionKnowledge(): Promise<{ items: KnowledgeEntry[] }> {
  return withoutFallback(async () => (await client.get<{ items: KnowledgeEntry[] }>(
    '/api/attribution/knowledge')).data);
}

export async function fetchDiagnosticAgents(): Promise<{ items: DiagnosticAgent[] }> {
  return withoutFallback(async () => (await client.get<{ items: DiagnosticAgent[] }>(
    '/api/attribution/diagnostic-agents')).data);
}

export async function fetchSkus(): Promise<Sku[]> {
  return withFallback(async () => (await client.get<Sku[]>('/api/skus')).data, () => mockSkus);
}

export async function fetchShops(): Promise<Shop[]> {
  return withFallback(async () => (await client.get<Shop[]>('/api/shops')).data, () => mockShops);
}

export async function runBatch(shopCode: string, date: string): Promise<ReplenishmentResult[]> {
  return withFallback(
    async () => (await client.post<ReplenishmentResult[]>('/api/replenish/batch', { shop_code: shopCode, date })).data,
    () => mockBatchResults.map((item) => ({ ...item, shop: shopCode || item.shop, flow: 'A' as const, engine: 'algo' as const })),
  );
}

export async function fetchAgentStatus(): Promise<AgentStatus> {
  try {
    return await runReadRequest(async () => (await client.get<AgentStatus>('/api/agent/status')).data, true);
  } catch {
    return { available: false, sdk_installed: false, endpoint_configured: false, model_configured: false, reason: 'backend unavailable' };
  }
}

export interface AgentBatchOutcome {
  results: ReplenishmentResult[];
  unavailable: boolean;
  reason?: string;
}

export async function runBatchAgent(shopCode: string, date: string): Promise<AgentBatchOutcome> {
  try {
    const { data } = await agentClient.post<ReplenishmentResult[] | { agent_unavailable?: boolean; reason?: string }>(
      '/api/replenish/agent/batch',
      { shop_code: shopCode, date },
    );
    if (Array.isArray(data)) {
      setMockMode(false);
      return { results: data.map((item) => ({ ...item, engine: item.engine ?? 'agent' })), unavailable: false };
    }
    return { results: [], unavailable: true, reason: data?.reason ?? 'agent unavailable' };
  } catch (error) {
    if (isAuthError(error)) throw error;
    const reason = error instanceof Error ? error.message : 'agent request failed';
    return { results: [], unavailable: true, reason };
  }
}

export async function fetchExceptions(): Promise<ExceptionItem[]> {
  return withFallback(async () => (await client.get<ExceptionItem[]>('/api/exceptions')).data, () => mockExceptions);
}

export async function fetchTrace(traceId: string): Promise<TraceDetail> {
  return withFallback(async () => (await client.get<TraceDetail>(`/api/trace/${traceId}`)).data, () => makeMockTrace(traceId));
}

export async function fetchRuns(): Promise<RunSummary[]> {
  return withFallback(async () => (await client.get<RunSummary[]>('/api/runs')).data, () => []);
}

export async function fetchRunDetail(runId: string): Promise<RunDetail | null> {
  return withFallback(
    async () => {
      const { data } = await client.get<RunDetail | { error: string }>(`/api/runs/${runId}`);
      return 'error' in data ? null : (data as RunDetail);
    },
    () => null,
  );
}

export async function clearRuns(): Promise<boolean> {
  try {
    await client.delete('/api/runs');
    setMockMode(false);
    return true;
  } catch (error) {
    console.warn('Failed to clear run history.', error);
    return false;
  }
}

// ---- Replenishment parameter configuration ----
// Reads use the shared mock fallback so the config panel always renders (with
// system defaults) even when the backend is down or serving an outdated build
// without the /api/config/* endpoints. Writes attempt the live backend first and
// only fall back to the in-memory mock store on network / missing-endpoint
// errors; genuine server validation errors are surfaced to the user.
export async function fetchConfigSchema(): Promise<ConfigSchema> {
  return withFallback(
    async () => (await client.get<ConfigSchema>('/api/config/schema')).data,
    () => mockConfigSchema(),
  );
}

export async function fetchConfigStatus(shopCode: string, goodsCode?: string): Promise<ConfigStatus> {
  const params: Record<string, string> = { shop_code: shopCode };
  if (goodsCode) params.goods_code = goodsCode;
  return withFallback(
    async () => (await client.get<ConfigStatus>('/api/config/status', { params })).data,
    () => mockConfigStatus(shopCode, goodsCode),
  );
}

export async function fetchEffectiveConfig(shopCode: string, goodsCode?: string): Promise<EffectiveConfig> {
  const params: Record<string, string> = { shop_code: shopCode };
  if (goodsCode) params.goods_code = goodsCode;
  return withFallback(
    async () => (await client.get<EffectiveConfig>('/api/config/effective', { params })).data,
    () => mockEffectiveConfig(shopCode, goodsCode),
  );
}

export async function saveStoreConfig(shopCode: string, params: ReplenishmentParams): Promise<ReplenishmentParams> {
  try {
    const { data } = await client.put<{ params?: ReplenishmentParams; error?: string }>('/api/config/store', { shop_code: shopCode, params });
    if (data?.error) throw new Error(data.error);
    setMockMode(false);
    return data.params ?? params;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Config backend unavailable, saving store config to mock store.', error);
    setMockMode(true);
    return mockSaveStoreConfig(shopCode, params);
  }
}

export async function saveSkuConfig(shopCode: string, goodsCode: string, params: ReplenishmentParams): Promise<ReplenishmentParams> {
  try {
    const { data } = await client.put<{ params?: ReplenishmentParams; error?: string }>(
      '/api/config/sku',
      { shop_code: shopCode, goods_code: goodsCode, params },
    );
    if (data?.error) throw new Error(data.error);
    setMockMode(false);
    return data.params ?? params;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Config backend unavailable, saving SKU config to mock store.', error);
    setMockMode(true);
    return mockSaveSkuConfig(shopCode, goodsCode, params);
  }
}

export async function clearStoreConfig(shopCode: string): Promise<boolean> {
  try {
    const { data } = await client.delete<{ removed: boolean }>(`/api/config/store/${shopCode}`);
    setMockMode(false);
    return !!data?.removed;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Config backend unavailable, clearing store config in mock store.', error);
    setMockMode(true);
    return mockClearStoreConfig(shopCode);
  }
}

export async function clearSkuConfig(shopCode: string, goodsCode: string): Promise<boolean> {
  try {
    const { data } = await client.delete<{ removed: boolean }>(`/api/config/sku/${shopCode}/${goodsCode}`);
    setMockMode(false);
    return !!data?.removed;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Config backend unavailable, clearing SKU config in mock store.', error);
    setMockMode(true);
    return mockClearSkuConfig(shopCode, goodsCode);
  }
}

export async function fetchStoreSkuConfigs(shopCode: string): Promise<StoreSkuConfig> {
  return withFallback(
    async () => (await client.get<StoreSkuConfig>('/api/config/store-skus', { params: { shop_code: shopCode } })).data,
    () => mockStoreSkuConfigs(shopCode),
  );
}

export async function saveSkuConfigBulk(shopCode: string, rows: SkuBulkItem[]): Promise<SkuBulkResult> {
  try {
    const { data } = await client.put<SkuBulkResult>('/api/config/sku/bulk', { shop_code: shopCode, rows });
    setMockMode(false);
    return data;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Config backend unavailable, saving SKU configs to mock store.', error);
    setMockMode(true);
    const saved = rows.map((r) => ({ goods_code: r.goods_code, params: mockSaveSkuConfig(shopCode, r.goods_code, r.params) }));
    return { shop_code: shopCode, saved, errors: [] };
  }
}

// ---- Current inventory feed (门店当前库存) ----
// Reads fall back to the deterministic synthetic mock feed so the editable
// inventory table always renders. Writes attempt the live backend first and only
// fall back to the in-memory mock overrides on network / missing-endpoint errors.
export async function fetchInventory(shopCode: string, date: string): Promise<StoreInventory> {
  return withFallback(
    async () => (await client.get<StoreInventory>('/api/inventory', { params: { shop_code: shopCode, date } })).data,
    () => mockInventory(shopCode, date),
  );
}

export async function saveInventory(
  shopCode: string,
  goodsCode: string,
  date: string,
  fields: InventoryFields,
): Promise<boolean> {
  try {
    await client.put('/api/inventory', { shop_code: shopCode, goods_code: goodsCode, fields });
    setMockMode(false);
    return true;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Inventory backend unavailable, saving to mock overrides.', error);
    setMockMode(true);
    mockSaveInventory(shopCode, goodsCode, fields);
    return true;
  }
}

export async function clearInventory(shopCode: string, goodsCode: string): Promise<boolean> {
  try {
    await client.delete(`/api/inventory/${shopCode}/${goodsCode}`);
    setMockMode(false);
    return true;
  } catch (error) {
    if (!shouldUseMockFallback(error)) throw error;
    console.warn('Inventory backend unavailable, clearing mock override.', error);
    setMockMode(true);
    mockClearInventory(shopCode, goodsCode);
    return true;
  }
}

// ---- Staff adjustment of order quantities ----
export async function adjustRun(
  runId: string,
  items: AdjustItem[],
  outputLanguage: AttributionOutputLanguage,
): Promise<AdjustResult> {
  const { data } = await client.post<AdjustResult>('/api/replenish/adjust', {
    run_id: runId,
    items,
    output_language: outputLanguage,
  });
  setMockMode(false);
  return data;
}

export async function fetchRunSubmissionReadiness(runId: string): Promise<RunSubmissionReadiness> {
  return withoutFallback(
    async () => (await client.get<RunSubmissionReadiness>(`/api/runs/${runId}/submission-readiness`)).data,
  );
}

export async function submitRun(runId: string, expectedVersion: number): Promise<RunSubmissionResult> {
  const { data } = await client.post<RunSubmissionResult>(
    `/api/runs/${runId}/submit`,
    { expected_version: expectedVersion },
  );
  setMockMode(false);
  return data;
}

export async function fetchAttributionJob(jobId: string): Promise<AttributionJob> {
  return withoutFallback(
    async () => (await client.get<AttributionJob>(`/api/attribution/jobs/${jobId}`)).data,
  );
}

export async function fetchAttributionCases(filters: AttributionCaseFilters = {}): Promise<AttributionCaseList> {
  return withoutFallback(
    async () => (await client.get<AttributionCaseList>('/api/attribution/cases', { params: filters })).data,
  );
}

export async function fetchAttributionCase(caseId: string): Promise<AttributionCaseDetail> {
  return withoutFallback(
    async () => (await client.get<AttributionCaseDetail>(`/api/attribution/cases/${caseId}`)).data,
  );
}

export async function downloadAttributionAttemptLog(
  caseId: string,
  attemptNumber: number,
): Promise<Blob> {
  const { data } = await client.get<Blob>(
    `/api/attribution/cases/${caseId}/attempts/${attemptNumber}/raw-log`,
    { responseType: 'blob' },
  );
  setMockMode(false);
  return data;
}

export async function submitAttributionReview(
  caseId: string,
  review: AttributionReviewRequest,
): Promise<AttributionCaseDetail> {
  const { data } = await client.post<AttributionCaseDetail>(
    `/api/attribution/cases/${caseId}/reviews`,
    review,
  );
  setMockMode(false);
  return data;
}

export async function retryAttributionCase(
  caseId: string,
  expectedVersion: number,
  outputLanguage: AttributionOutputLanguage,
): Promise<AttributionCaseDetail> {
  const { data } = await client.post<AttributionCaseDetail>(
    `/api/attribution/cases/${caseId}/retry`,
    { expected_version: expectedVersion, output_language: outputLanguage },
  );
  setMockMode(false);
  return data;
}

export async function cancelAttributionCase(caseId: string, expectedVersion: number): Promise<AttributionCaseDetail> {
  const { data } = await client.post<AttributionCaseDetail>(
    `/api/attribution/cases/${caseId}/cancel`,
    { expected_version: expectedVersion },
  );
  setMockMode(false);
  return data;
}

export async function fetchAttributionReviewCount(): Promise<AttributionReviewCount> {
  return withoutFallback(
    async () => (await client.get<AttributionReviewCount>('/api/attribution/review-count')).data,
  );
}
