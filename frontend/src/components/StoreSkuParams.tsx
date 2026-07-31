import { QuestionCircleOutlined, ReloadOutlined, SaveOutlined, UndoOutlined } from '@/components/ui/icons';
import { Alert, Button, Card, Empty, InputNumber, Space, Spin, Table, Tag, Tooltip, Typography, message } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  clearSkuConfig,
  fetchEffectiveConfig,
  fetchStoreSkuConfigs,
  saveSkuConfig,
  saveSkuConfigBulk,
  saveStoreConfig,
} from '../api';
import { useI18n } from '../i18n';
import type { ParamSpec, ReplenishmentParams, StoreSkuRow } from '../types';

const { Text, Title } = Typography;

const toDisplay = (spec: ParamSpec, val: number): number =>
  spec.type === 'percent' ? Math.round(val * 10000) / 100 : val;
const fromDisplay = (spec: ParamSpec, val: number): number =>
  spec.type === 'percent' ? Math.round((val / 100) * 10000) / 10000 : val;
const precisionOf = (spec: ParamSpec): number => (spec.type === 'int' ? 0 : 2);

type EditMap = Record<string, Record<string, number>>;

const rowToEdits = (params: ParamSpec[], values: ReplenishmentParams): Record<string, number> => {
  const e: Record<string, number> = {};
  params.forEach((p) => { e[p.key] = toDisplay(p, Number(values[p.key] ?? p.default)); });
  return e;
};

const editsToParams = (params: ParamSpec[], ed: Record<string, number>): ReplenishmentParams => {
  const out: ReplenishmentParams = {};
  params.forEach((p) => { out[p.key] = fromDisplay(p, Number(ed[p.key])); });
  return out;
};

const skuOverridden = (r: StoreSkuRow): boolean => !!r.sku && Object.keys(r.sku).length > 0;

interface Props {
  shopCode: string;
  shopName?: string;
}

export default function StoreSkuParams({ shopCode, shopName }: Props) {
  const { t, lang } = useI18n();
  const [params, setParams] = useState<ParamSpec[]>([]);
  const [rows, setRows] = useState<StoreSkuRow[]>([]);
  const [edits, setEdits] = useState<EditMap>({});
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [savingAll, setSavingAll] = useState(false);
  const [savingRows, setSavingRows] = useState<Set<string>>(new Set());

  // Store-level (shared) params: service level, lead time, review period.
  const [storeEdits, setStoreEdits] = useState<Record<string, number>>({});
  const [storeDirty, setStoreDirty] = useState(false);
  const [storeConfigured, setStoreConfigured] = useState(false);
  const [savingStore, setSavingStore] = useState(false);

  const storeParams = useMemo(() => params.filter((p) => p.scope === 'store'), [params]);
  const skuParams = useMemo(() => params.filter((p) => p.scope === 'sku'), [params]);

  const label = useCallback((p: ParamSpec) => (lang === 'en' ? p.label_en : p.label), [lang]);
  const help = useCallback((p: ParamSpec) => (lang === 'en' ? p.help_en : p.help), [lang]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchStoreSkuConfigs(shopCode);
      setParams(data.params);
      setRows(data.rows);
      const sku = data.params.filter((p) => p.scope === 'sku');
      const store = data.params.filter((p) => p.scope === 'store');
      const nextEdits: EditMap = {};
      data.rows.forEach((r) => { nextEdits[r.goods_code] = rowToEdits(sku, r.effective); });
      setEdits(nextEdits);
      setDirty(new Set());
      // Seed the store form from the explicit store config, falling back to
      // system defaults when the store was never configured.
      const storeVals: ReplenishmentParams = { ...(data.store ?? {}) };
      setStoreEdits(rowToEdits(store, storeVals));
      setStoreConfigured(!!data.store);
      setStoreDirty(false);
    } catch {
      message.error(t('cfg.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [shopCode, t]);

  useEffect(() => { void load(); }, [load]);

  const markDirty = (gc: string) => setDirty((prev) => (prev.has(gc) ? prev : new Set(prev).add(gc)));
  const clearDirty = (gc: string) => setDirty((prev) => { const n = new Set(prev); n.delete(gc); return n; });
  const setSaving = (gc: string, on: boolean) =>
    setSavingRows((prev) => { const n = new Set(prev); if (on) n.add(gc); else n.delete(gc); return n; });

  const applyRow = (gc: string, next: Partial<StoreSkuRow>) =>
    setRows((prev) => prev.map((r) => (r.goods_code === gc ? { ...r, ...next } : r)));

  const saveStore = async () => {
    setSavingStore(true);
    try {
      const saved = await saveStoreConfig(shopCode, editsToParams(storeParams, storeEdits));
      setStoreEdits(rowToEdits(storeParams, saved));
      setStoreConfigured(true);
      setStoreDirty(false);
      message.success(t('param.storeSaved'));
    } catch (e) {
      message.error(t('cfg.saveFailed', { reason: e instanceof Error ? e.message : '' }));
    } finally {
      setSavingStore(false);
    }
  };

  const saveRow = async (gc: string) => {
    setSaving(gc, true);
    try {
      const saved = await saveSkuConfig(shopCode, gc, editsToParams(skuParams, edits[gc]));
      applyRow(gc, { level: 'sku', sku: saved, effective: { ...rowsEffective(gc), ...saved } });
      setEdits((prev) => ({ ...prev, [gc]: rowToEdits(skuParams, saved) }));
      clearDirty(gc);
      message.success(t('param.rowSaved', { sku: gc }));
    } catch (e) {
      message.error(t('cfg.saveFailed', { reason: e instanceof Error ? e.message : '' }));
    } finally {
      setSaving(gc, false);
    }
  };

  const rowsEffective = (gc: string): ReplenishmentParams =>
    rows.find((r) => r.goods_code === gc)?.effective ?? {};

  const resetRow = async (gc: string) => {
    setSaving(gc, true);
    try {
      await clearSkuConfig(shopCode, gc);
      const eff = await fetchEffectiveConfig(shopCode, gc);
      applyRow(gc, { level: eff.store ? 'store' : 'none', sku: null, effective: eff.effective });
      setEdits((prev) => ({ ...prev, [gc]: rowToEdits(skuParams, eff.effective) }));
      clearDirty(gc);
      message.success(t('param.resetDone', { sku: gc }));
    } catch {
      message.error(t('cfg.removeFailed'));
    } finally {
      setSaving(gc, false);
    }
  };

  const saveAll = async () => {
    const targets = [...dirty];
    if (targets.length === 0) return;
    setSavingAll(true);
    try {
      const payload = targets.map((gc) => ({ goods_code: gc, params: editsToParams(skuParams, edits[gc]) }));
      const res = await saveSkuConfigBulk(shopCode, payload);
      res.saved.forEach((s) => {
        applyRow(s.goods_code, { level: 'sku', sku: s.params, effective: { ...rowsEffective(s.goods_code), ...s.params } });
        setEdits((prev) => ({ ...prev, [s.goods_code]: rowToEdits(skuParams, s.params) }));
        clearDirty(s.goods_code);
      });
      if (res.errors.length > 0) {
        message.warning(t('param.someFailed', { n: res.errors.length }));
      } else {
        message.success(t('param.allSaved', { n: res.saved.length }));
      }
    } catch (e) {
      message.error(t('cfg.saveFailed', { reason: e instanceof Error ? e.message : '' }));
    } finally {
      setSavingAll(false);
    }
  };

  const skuTag = (r: StoreSkuRow) =>
    skuOverridden(r) ? <Tag color="geekblue">{t('cfg.levelSku')}</Tag> : <Tag>{t('cfg.levelNone')}</Tag>;

  const columns = useMemo<ColumnsType<StoreSkuRow>>(() => {
    const skuCol: ColumnsType<StoreSkuRow>[number] = {
      title: t('cfg.colSku'),
      key: 'sku',
      fixed: 'left',
      width: 240,
      render: (_v, r) => (
        <Space direction="vertical" size={2}>
          <Text strong>{r.goods_name}</Text>
          <Space size={6} wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>{r.goods_code}</Text>
            {skuTag(r)}
            {dirty.has(r.goods_code) && <Tag color="orange">{t('param.unsaved')}</Tag>}
          </Space>
        </Space>
      ),
    };

    const paramCols: ColumnsType<StoreSkuRow> = skuParams.map((p) => ({
      title: (
        <Space size={4}>
          <span>{label(p)}</span>
          <Tooltip title={help(p)}><QuestionCircleOutlined style={{ color: 'var(--text-muted)' }} /></Tooltip>
        </Space>
      ),
      key: p.key,
      align: 'right',
      width: 138,
      render: (_v, r) => (
        <InputNumber
          style={{ width: '100%' }}
          value={edits[r.goods_code]?.[p.key]}
          min={toDisplay(p, p.min)}
          max={toDisplay(p, p.max)}
          step={p.type === 'percent' ? p.step * 100 : p.step}
          precision={precisionOf(p)}
          addonAfter={p.type === 'percent' ? '%' : undefined}
          onChange={(v) => {
            if (v == null) return;
            setEdits((prev) => ({ ...prev, [r.goods_code]: { ...prev[r.goods_code], [p.key]: Number(v) } }));
            markDirty(r.goods_code);
          }}
        />
      ),
    }));

    const opsCol: ColumnsType<StoreSkuRow>[number] = {
      title: t('cfg.colOps'),
      key: 'ops',
      fixed: 'right',
      width: 172,
      render: (_v, r) => (
        <Space size={4}>
          <Button
            type="primary"
            size="small"
            icon={<SaveOutlined />}
            loading={savingRows.has(r.goods_code)}
            onClick={() => void saveRow(r.goods_code)}
          >
            {t('param.saveRow')}
          </Button>
          <Tooltip title={t('param.reset')}>
            <Button
              size="small"
              icon={<UndoOutlined />}
              disabled={!skuOverridden(r) || savingRows.has(r.goods_code)}
              onClick={() => void resetRow(r.goods_code)}
            />
          </Tooltip>
        </Space>
      ),
    };

    return [skuCol, ...paramCols, opsCol];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skuParams, edits, dirty, savingRows, lang, t]);

  if (loading) {
    return <div style={{ padding: 48, textAlign: 'center' }}><Spin tip={t('cfg.loading')} /></div>;
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* Store-level parameters (shared by every SKU in the store). */}
      <Card
        size="small"
        title={
          <Space size={8}>
            <span>{t('param.storeSectionTitle')}</span>
            {storeConfigured
              ? <Tag color="blue">{t('cfg.levelStore')}</Tag>
              : <Tag>{t('cfg.levelNone')}</Tag>}
          </Space>
        }
        extra={
          <Button
            type="primary"
            size="small"
            icon={<SaveOutlined />}
            loading={savingStore}
            disabled={!storeDirty}
            onClick={() => void saveStore()}
          >
            {t('param.saveStore')}
          </Button>
        }
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>{t('param.storeSectionHint')}</Text>
        <Space size="large" wrap>
          {storeParams.map((p) => (
            <div key={p.key}>
              <Space size={4} style={{ marginBottom: 4 }}>
                <Text>{label(p)}</Text>
                <Tooltip title={help(p)}><QuestionCircleOutlined style={{ color: 'var(--text-muted)' }} /></Tooltip>
              </Space>
              <div>
                <InputNumber
                  style={{ width: 160 }}
                  value={storeEdits[p.key]}
                  min={toDisplay(p, p.min)}
                  max={toDisplay(p, p.max)}
                  step={p.type === 'percent' ? p.step * 100 : p.step}
                  precision={precisionOf(p)}
                  addonAfter={p.type === 'percent' ? '%' : undefined}
                  onChange={(v) => {
                    if (v == null) return;
                    setStoreEdits((prev) => ({ ...prev, [p.key]: Number(v) }));
                    setStoreDirty(true);
                  }}
                />
              </div>
            </div>
          ))}
        </Space>
      </Card>

      {/* SKU-level parameters (per store+SKU). */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <Title level={5} style={{ margin: 0 }}>{t('param.tableTitle', { shop: shopName || shopCode })}</Title>
          <Text type="secondary">{t('param.skuSectionHint')}</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>{t('app.refresh')}</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={savingAll} disabled={dirty.size === 0} onClick={() => void saveAll()}>
            {t('param.saveAll', { n: dirty.size })}
          </Button>
        </Space>
      </div>

      {rows.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('param.noSkus')} />
      ) : (
        <Table
          size="small"
          rowKey="goods_code"
          columns={columns}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 'max-content' }}
        />
      )}

      <Alert type="info" showIcon message={t('param.resolveNote')} />
    </Space>
  );
}
