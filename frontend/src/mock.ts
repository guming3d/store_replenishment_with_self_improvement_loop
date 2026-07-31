import type {
  AdjustItem,
  AdjustResult,
  ConfigSchema,
  ConfigStatus,
  EffectiveConfig,
  ExceptionItem,
  InventoryFields,
  InventoryRow,
  ParamSpec,
  ReplenishmentParams,
  ReplenishmentResult,
  Shop,
  Sku,
  StoreInventory,
  StoreSkuConfig,
  TraceDetail,
} from './types';

export const mockSkus: Sku[] = [
  { goods_code: 'SKU-1001', goods_name: '有机鲜牛奶 950ml', category: 'fresh' },
  { goods_code: 'SKU-1002', goods_name: '青岛啤酒 500ml*12', category: 'beer' },
  { goods_code: 'SKU-1003', goods_name: '抽纸家庭装', category: 'paper' },
  { goods_code: 'SKU-1004', goods_name: '冷冻水饺 1kg', category: 'frozen' },
  { goods_code: 'SKU-1005', goods_name: '夏季气泡水', category: 'drinks' },
  { goods_code: 'SKU-1006', goods_name: '洗衣凝珠 40颗', category: 'home-clean' },
];

export const mockShops: Shop[] = [
  { shop_code: 'SHOP-001', shop_name: '上海长宁店', city: '上海' },
  { shop_code: 'SHOP-002', shop_name: '北京朝阳店', city: '北京' },
  { shop_code: 'SHOP-003', shop_name: '广州天河店', city: '广州' },
  { shop_code: 'SHOP-004', shop_name: '成都高新店', city: '成都' },
];

const mkInv = (on_hand: number, in_transit = 0, reserved = 0, expiring = 0, zero = 0) => ({
  on_hand, in_transit, reserved, expiring, recent_zero_days: zero,
  available: Math.max(0, on_hand + in_transit - reserved - expiring),
  phantom_suspect: false, source: 'synthetic' as const, overridden: [] as string[],
});

export const mockBatchResults: ReplenishmentResult[] = [
  {
    shop: 'SHOP-001', sku: 'SKU-1001', scenario: 'fresh', chosen_qty: 20, final_qty: 20, safety_stock: 12, target_stock: 32,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: true, trigger: true, reorder_point: 24, order_up_to: 40, position: 18,
    inventory: mkInv(18, 0, 0, 0, 0),
    candidates: [{ qty: 18, method: 'safety-stock', risk: '短保损耗低' }, { qty: 24, method: 'monte-carlo', risk: '缺货概率 8%' }],
    explanation: '鲜奶短保且近 3 日需求上升，库存位置 18 低于补货点 24，补足到目标位 40，推荐补货 20 件。',
    trace_id: 'TRACE-FRESH-001', exception: false,
  },
  {
    shop: 'SHOP-001', sku: 'SKU-1002', scenario: 'promo', chosen_qty: 54, final_qty: 54, safety_stock: 30, target_stock: 86,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: true, trigger: true, reorder_point: 40, order_up_to: 90, position: 36,
    inventory: mkInv(36, 0, 0, 0, 0),
    candidates: [{ qty: 42, method: 'target-stock', risk: '促销峰值覆盖不足' }, { qty: 60, method: 'monte-carlo', risk: '库存周转偏高' }],
    explanation: '周末啤酒促销叠加高温天气，促销技能给出 +12 件增量，推荐补货 54 件。',
    trace_id: 'TRACE-PROMO-002', exception: true,
  },
  {
    shop: 'SHOP-001', sku: 'SKU-1003', scenario: 'standard', chosen_qty: 0, final_qty: 0, safety_stock: 18, target_stock: 58,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: false, trigger: false, reorder_point: 20, order_up_to: 58, position: 42,
    inventory: mkInv(42, 6, 0, 0, 0),
    candidates: [{ qty: 25, method: 'rounding', risk: '整箱约束' }, { qty: 22, method: 'safety-stock', risk: '风险稳定' }],
    explanation: '家庭纸品库存位置 42 高于补货点 20，今日无需补货。',
    trace_id: 'TRACE-STANDARD-003', exception: false,
  },
  {
    shop: 'SHOP-001', sku: 'SKU-1004', scenario: 'stockout', chosen_qty: 72, final_qty: 72, safety_stock: 36, target_stock: 96,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: true, trigger: true, reorder_point: 44, order_up_to: 96, position: 0,
    inventory: mkInv(0, 0, 0, 0, 5),
    candidates: [{ qty: 80, method: 'target-stock', risk: '当前库存为 0' }, { qty: 72, method: 'monte-carlo', risk: '冷链仓容紧张' }],
    explanation: '冷冻水饺发生断货，库存位置 0，算法建议快速回补；受冷链仓容约束取 72 件。',
    trace_id: 'TRACE-STOCKOUT-004', exception: true,
  },
  {
    shop: 'SHOP-001', sku: 'SKU-1005', scenario: 'season', chosen_qty: 36, final_qty: 36, safety_stock: 20, target_stock: 62,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: true, trigger: true, reorder_point: 28, order_up_to: 62, position: 24,
    inventory: mkInv(24, 0, 0, 0, 0),
    candidates: [{ qty: 36, method: 'season', risk: '气温驱动需求' }, { qty: 30, method: 'safety-stock', risk: '常规覆盖' }],
    explanation: '气泡水进入夏季高峰，库存位置 24 低于补货点 28，季节技能给出 +6 件增量。',
    trace_id: 'TRACE-SEASON-005', exception: false,
  },
  {
    shop: 'SHOP-001', sku: 'SKU-1006', scenario: 'longtail', chosen_qty: 0, final_qty: 0, safety_stock: 6, target_stock: 16,
    flow: 'A', lead_time: 2, apply_date: '2024-06-01', arrival_date: '2024-06-02', shelf_date: '2024-06-03',
    triggered: false, trigger: false, reorder_point: 8, order_up_to: 16, position: 14,
    inventory: mkInv(14, 0, 0, 0, 3),
    candidates: [{ qty: 8, method: 'param-learn', risk: '慢动销' }, { qty: 12, method: 'rounding', risk: '整箱补货' }],
    explanation: '洗衣凝珠为长尾慢动销，库存位置 14 高于补货点 8，今日无需补货。',
    trace_id: 'TRACE-LONGTAIL-006', exception: false,
  },
];

export const mockExceptions: ExceptionItem[] = [
  { ...mockBatchResults[1], override_type: 'high', reason: '促销 + 高温，推荐量高于门店均值 45%', suggested_action: '复核促销计划和端架陈列容量' },
  { ...mockBatchResults[3], override_type: 'low', reason: '冷链仓容限制，断货回补被下调', suggested_action: '协调仓配或拆分到次日补货' },
];

export const makeMockTrace = (traceId = 'TRACE-PROMO-002'): TraceDetail => {
  const source = mockBatchResults.find((item) => item.trace_id === traceId) ?? mockBatchResults[1];
  return {
    trace_id: traceId,
    shop: source.shop,
    sku: source.sku,
    scenario: source.scenario,
    final_qty: source.chosen_qty,
    summary: source.explanation,
    steps: [
      { step: 1, name: '读取预测缓存', skill: 'param-learn', type: 'algo', input: 'shop + sku + date', output: 'mean=38, p90=55', delta: 0 },
      { step: 2, name: '计算安全库存', skill: 'safety-stock', type: 'algo', input: '服务水平 95%', output: `安全库存 ${source.safety_stock}`, delta: 0 },
      { step: 3, name: '识别业务场景', skill: source.scenario, type: source.scenario === 'standard' ? 'algo' : 'soft', input: '节假日、促销、新品、季节特征', output: `场景=${source.scenario}`, delta: source.scenario === 'standard' ? 0 : 8 },
      { step: 4, name: '候选方案仿真', skill: 'monte-carlo', type: 'algo', input: '500 次需求抽样', output: source.candidates.map((candidate) => `${candidate.qty}/${candidate.method}`).join('；'), delta: 0 },
      { step: 5, name: '整箱取整与风险裁决', skill: 'rounding', type: 'algo', input: '候选数量 + 仓容约束', output: `推荐 ${source.chosen_qty}`, delta: source.chosen_qty - source.candidates[0].qty },
    ],
  };
};

// ---- Replenishment parameter configuration (mock / offline fallback) --------
// Mirrors backend PARAM_SPECS so the config panel renders identically when the
// backend is unavailable or serving an outdated build without /api/config/*.
export const mockParamSpecs: ParamSpec[] = [
  { key: 'fill_rate', type: 'percent', scope: 'store', default: 0.9, min: 0.5, max: 0.9999, step: 0.01,
    label: '服务水平(填充率)', label_en: 'Service Level (Fill Rate)',
    help: '目标周期服务水平, 越高安全库存/补货点越高', help_en: 'Target cycle service level; higher means a higher safety stock / reorder point' },
  { key: 'coverage', type: 'int', scope: 'store', default: 7, min: 1, max: 90, step: 1,
    label: '目标覆盖天数', label_en: 'Target Coverage (days)',
    help: '触发补货时一次补足到可覆盖的目标销售天数', help_en: 'When a reorder triggers, order up to cover this many days of demand' },
  { key: 'case_pack', type: 'int', scope: 'sku', default: 6, min: 1, max: 1000, step: 1,
    label: '箱规/最小包装量', label_en: 'Case Pack',
    help: '补货数量向上取整到该包装规格的整数倍', help_en: 'Order qty is rounded up to a multiple of this pack size' },
  { key: 'moq', type: 'int', scope: 'sku', default: 0, min: 0, max: 10000, step: 1,
    label: '最小起订量(MOQ)', label_en: 'Min Order Qty (MOQ)',
    help: '供应商要求的最小起订量', help_en: 'Supplier minimum order quantity' },
  { key: 'shelf_max', type: 'int', scope: 'sku', default: 999, min: 1, max: 100000, step: 1,
    label: '货架最大陈列量', label_en: 'Shelf Max Capacity',
    help: '门店货架/仓容上限, 补货数量不超过该值', help_en: 'Shelf / capacity cap; order qty never exceeds this' },
];

// Scope split mirrors the backend: store config keeps only STORE_KEYS, SKU
// overrides keep only SKU_KEYS.
const mockStoreKeys: string[] = mockParamSpecs.filter((s) => s.scope === 'store').map((s) => s.key);
const mockSkuKeys: string[] = mockParamSpecs.filter((s) => s.scope === 'sku').map((s) => s.key);

const pickKeys = (params: ReplenishmentParams, keys: string[]): ReplenishmentParams => {
  const out: ReplenishmentParams = {};
  for (const k of keys) {
    if (params[k] !== undefined && params[k] !== null) out[k] = params[k];
  }
  return out;
};

export const mockDefaults: ReplenishmentParams = Object.fromEntries(
  mockParamSpecs.map((spec) => [spec.key, spec.default]),
);

// In-memory store so "save" works end-to-end while offline within a session.
const mockConfigStore: {
  store: Record<string, ReplenishmentParams>;
  sku: Record<string, Record<string, ReplenishmentParams>>;
} = { store: {}, sku: {} };

export const mockConfigSchema = (): ConfigSchema => ({
  params: mockParamSpecs,
  defaults: { ...mockDefaults },
});

export const mockConfigStatus = (shopCode: string, goodsCode?: string): ConfigStatus => {
  const storeSet = !!mockConfigStore.store[shopCode];
  if (goodsCode) {
    const skuSet = !!mockConfigStore.sku[shopCode]?.[goodsCode];
    return {
      configured: skuSet || storeSet,
      level: skuSet ? 'sku' : storeSet ? 'store' : 'none',
      shop_code: shopCode,
      goods_code: goodsCode,
    };
  }
  const skuAny = Object.keys(mockConfigStore.sku[shopCode] ?? {}).length > 0;
  return {
    configured: storeSet || skuAny,
    level: storeSet ? 'store' : skuAny ? 'sku' : 'none',
    shop_code: shopCode,
    goods_code: null,
  };
};

export const mockEffectiveConfig = (shopCode: string, goodsCode?: string): EffectiveConfig => {
  const store = mockConfigStore.store[shopCode] ?? null;
  const sku = goodsCode ? mockConfigStore.sku[shopCode]?.[goodsCode] ?? null : null;
  return {
    shop_code: shopCode,
    goods_code: goodsCode ?? null,
    effective: { ...mockDefaults, ...(store ?? {}), ...(sku ?? {}) },
    store,
    sku,
    sku_overrides: { ...(mockConfigStore.sku[shopCode] ?? {}) },
  };
};

export const mockSaveStoreConfig = (shopCode: string, params: ReplenishmentParams): ReplenishmentParams => {
  // Store config keeps only store-scoped keys, with defaults filled in.
  const cleaned = pickKeys(params, mockStoreKeys);
  const full: ReplenishmentParams = {};
  for (const k of mockStoreKeys) full[k] = cleaned[k] ?? mockDefaults[k];
  mockConfigStore.store[shopCode] = full;
  return { ...full };
};

export const mockSaveSkuConfig = (
  shopCode: string,
  goodsCode: string,
  params: ReplenishmentParams,
): ReplenishmentParams => {
  // SKU overrides keep only sku-scoped keys.
  const cleaned = pickKeys(params, mockSkuKeys);
  const overrides = mockConfigStore.sku[shopCode] ?? {};
  if (Object.keys(cleaned).length === 0) {
    delete overrides[goodsCode];
  } else {
    overrides[goodsCode] = { ...cleaned };
  }
  mockConfigStore.sku[shopCode] = overrides;
  return { ...cleaned };
};

export const mockClearStoreConfig = (shopCode: string): boolean => {
  const had = !!mockConfigStore.store[shopCode];
  delete mockConfigStore.store[shopCode];
  return had;
};

export const mockClearSkuConfig = (shopCode: string, goodsCode: string): boolean => {
  const overrides = mockConfigStore.sku[shopCode];
  const had = !!overrides?.[goodsCode];
  if (overrides) delete overrides[goodsCode];
  return had;
};

// ---- Current inventory feed (mock / offline fallback) ----------------------
// Deterministic pseudo-random on-hand so the editable inventory table renders
// consistently offline, mirroring the backend synthetic feed shape.
const mockInvOverrides: Record<string, Record<string, InventoryFields>> = {};

const hashSeed = (s: string): number => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
};

const synthInv = (shopCode: string, goodsCode: string, date: string) => {
  const h = hashSeed(`${shopCode}|${goodsCode}|${date}`);
  const on_hand = h % 60;
  const in_transit = (h >> 6) % 12;
  const reserved = (h >> 12) % 4;
  const expiring = (h >> 16) % 3;
  const recent_zero_days = (h >> 20) % 6;
  const days_to_expiry = expiring > 0 ? ((h >> 24) % 5) + 1 : 0;
  return { on_hand, in_transit, reserved, expiring, days_to_expiry, recent_zero_days };
};

// Rough daily mean for the DEV mock so the expiry-aware available mirrors the backend.
const mockDailyMean = (goodsCode: string): number => (hashSeed(goodsCode) % 6) + 1;

const mockAvailable = (r: {
  on_hand: number; in_transit: number; reserved: number; expiring: number;
  days_to_expiry: number; daily_mean: number;
}): number => {
  const sellable = Math.min(r.expiring, Math.floor(Math.max(0, r.daily_mean) * Math.max(0, r.days_to_expiry)));
  return Math.max(0, r.on_hand + r.in_transit - r.reserved - (r.expiring - sellable));
};

export const mockInventory = (shopCode: string, date: string): StoreInventory => {
  const overrides = mockInvOverrides[shopCode] ?? {};
  const rows: InventoryRow[] = mockSkus.map((s) => {
    const base = synthInv(shopCode, s.goods_code, date);
    const ov = overrides[s.goods_code];
    const merged = { ...base, ...(ov ?? {}) };
    const overridden = ov ? Object.keys(ov) : [];
    const daily_mean = mockDailyMean(s.goods_code);
    return {
      goods_code: s.goods_code,
      goods_name: s.goods_name,
      category: s.category,
      on_hand: merged.on_hand,
      in_transit: merged.in_transit,
      reserved: merged.reserved,
      expiring: merged.expiring,
      days_to_expiry: merged.days_to_expiry,
      recent_zero_days: merged.recent_zero_days,
      available: mockAvailable({ ...merged, daily_mean }),
      daily_mean,
      source: overridden.length ? 'override' : 'synthetic',
      overridden,
    };
  });
  return { shop_code: shopCode, date, rows };
};

export const mockSaveInventory = (
  shopCode: string,
  goodsCode: string,
  fields: InventoryFields,
): void => {
  const store = mockInvOverrides[shopCode] ?? {};
  store[goodsCode] = { ...(store[goodsCode] ?? {}), ...fields };
  mockInvOverrides[shopCode] = store;
};

export const mockClearInventory = (shopCode: string, goodsCode: string): void => {
  const store = mockInvOverrides[shopCode];
  if (store) delete store[goodsCode];
};

// ---- Staff adjustment of order quantities (mock / offline fallback) --------
export const mockAdjust = (
  results: ReplenishmentResult[],
  items: AdjustItem[],
): AdjustResult => {
  const map = new Map(items.map((i) => [i.sku, i.final_qty]));
  let changed = 0;
  const updated = results.map((r) => {
    if (map.has(r.sku)) {
      const q = Math.max(0, Math.round(map.get(r.sku) as number));
      if (q !== r.final_qty) changed += 1;
      return { ...r, final_qty: q };
    }
    return r;
  });
  const total_qty = updated.reduce((sum, r) => sum + (r.final_qty ?? 0), 0);
  return { run_id: 'MOCK-RUN', changed, total_qty, results: updated };
};

// The store's SKU assortment with per-SKU resolved + explicit params. In mock
// mode every catalogue SKU is treated as belonging to the store.
export const mockStoreSkuConfigs = (shopCode: string): StoreSkuConfig => ({
  shop_code: shopCode,
  store: mockEffectiveConfig(shopCode).store,
  params: mockParamSpecs,
  rows: mockSkus.map((s) => {
    const eff = mockEffectiveConfig(shopCode, s.goods_code);
    return {
      goods_code: s.goods_code,
      goods_name: s.goods_name,
      category: s.category,
      level: mockConfigStatus(shopCode, s.goods_code).level,
      effective: eff.effective,
      sku: eff.sku,
    };
  }),
});
