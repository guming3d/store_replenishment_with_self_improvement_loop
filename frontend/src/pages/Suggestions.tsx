import { FileDoneOutlined, ReloadOutlined, SaveOutlined, SearchOutlined, SettingOutlined } from '@/components/ui/icons';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  adjustRun,
  clearInventory,
  fetchInventory,
  fetchRunSubmissionReadiness,
  runBatch,
  runBatchAgent,
  saveInventory,
  submitRun,
} from '../api';
import { useStoreContext } from '../App';
import ConfigPanel from '../components/ConfigPanel';
import ReplenishmentResultDetails from '../components/ReplenishmentResultDetails';
import { RunGateStatusTag } from '../components/AttributionStatusTag';
import { useI18n } from '../i18n';
import type { InventoryRow, ReplenishmentResult, RunSubmissionReadiness, Scenario } from '../types';

const { Text, Title } = Typography;

const scenarioColors: Record<Scenario, string> = {
  standard: 'blue', fresh: 'green', longtail: 'purple', new: 'cyan', promo: 'orange', holiday: 'magenta', season: 'lime', stockout: 'red',
};

const scenarioKeys: Scenario[] = ['standard', 'fresh', 'longtail', 'new', 'promo', 'holiday', 'season', 'stockout'];

const today = () => new Date().toISOString().slice(0, 10);

const sellableExpiring = (r: InventoryRow): number => {
  const mean = Math.max(0, r.daily_mean ?? 0);
  const days = Math.max(0, r.days_to_expiry ?? 0);
  return Math.min(r.expiring, Math.floor(mean * days));
};

// Mirror the backend expiry-aware position: only the part of the near-expiry
// lot we can't sell before it perishes is deducted.
const recomputeAvailable = (r: InventoryRow): number =>
  Math.max(0, r.on_hand + r.in_transit - r.reserved - (r.expiring - sellableExpiring(r)));

const finalOf = (r: ReplenishmentResult): number => r.final_qty ?? r.chosen_qty ?? 0;

export default function Suggestions() {
  const { shops, skus, selectedShop, selectedSku, batchResults, setBatchResults, engineMode, agentStatus } = useStoreContext();
  const { t, lang, scenarioLabel } = useI18n();
  const navigate = useNavigate();
  const [date, setDate] = useState(today());
  const [loading, setLoading] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);

  // Auto-loaded, staff-editable current inventory for the selected store.
  const [inv, setInv] = useState<InventoryRow[]>([]);
  const [invLoading, setInvLoading] = useState(false);
  const [invDirty, setInvDirty] = useState<Set<string>>(new Set());
  const [savingRow, setSavingRow] = useState<string | null>(null);

  // Staff overrides of the recommended final order quantity.
  const [qtyEdits, setQtyEdits] = useState<Record<string, number>>({});
  const [adjusting, setAdjusting] = useState(false);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasonCode, setReasonCode] = useState<string>();
  const [reasonText, setReasonText] = useState('');
  const [readiness, setReadiness] = useState<RunSubmissionReadiness>();
  const [submittingRun, setSubmittingRun] = useState(false);

  const skuNameMap = useMemo(() => new Map(skus.map((sku) => [sku.goods_code, sku.goods_name])), [skus]);
  const selectedShopName = shops.find((shop) => shop.shop_code === selectedShop)?.shop_name ?? selectedShop;
  const visibleResults = useMemo(
    () => (selectedSku ? batchResults.filter((item) => item.sku === selectedSku) : batchResults),
    [batchResults, selectedSku],
  );

  // ---- Auto-fetch current inventory whenever the store / date changes -------
  const loadInventory = useCallback(async () => {
    if (!selectedShop) { setInv([]); return; }
    setInvLoading(true);
    try {
      const data = await fetchInventory(selectedShop, date);
      setInv(data.rows);
      setInvDirty(new Set());
    } catch (error) {
      setInv([]);
      message.error(t('sug.inventoryLoadFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setInvLoading(false);
    }
  }, [selectedShop, date]);

  useEffect(() => { void loadInventory(); }, [loadInventory]);

  const editInv = (gc: string, field: keyof InventoryRow, val: number | null) => {
    if (val == null) return;
    setInv((rows) => rows.map((r) => {
      if (r.goods_code !== gc) return r;
      const next = { ...r, [field]: Math.max(0, Math.round(val)) } as InventoryRow;
      next.available = recomputeAvailable(next);
      return next;
    }));
    setInvDirty((d) => new Set(d).add(gc));
  };

  const saveInvRow = async (row: InventoryRow) => {
    if (!selectedShop) return;
    setSavingRow(row.goods_code);
    try {
      await saveInventory(selectedShop, row.goods_code, date, {
        on_hand: row.on_hand,
        in_transit: row.in_transit,
        reserved: row.reserved,
        expiring: row.expiring,
        days_to_expiry: row.days_to_expiry,
        recent_zero_days: row.recent_zero_days,
      });
      message.success(t('sug.invSaved', { sku: row.goods_code }));
      await loadInventory();
    } finally {
      setSavingRow(null);
    }
  };

  const resetInvRow = async (row: InventoryRow) => {
    if (!selectedShop) return;
    await clearInventory(selectedShop, row.goods_code);
    message.success(t('sug.invReset', { sku: row.goods_code }));
    await loadInventory();
  };

  // ---- Run the automatic (s,S) continuous-review replenishment -------------
  const doRun = async () => {
    if (!selectedShop) return;
    setLoading(true);
    try {
      if (engineMode === 'agent' && agentStatus?.available) {
        const hide = message.loading(t('sug.agentRunning'), 0);
        const outcome = await runBatchAgent(selectedShop, date);
        hide();
        if (outcome.unavailable) {
          message.warning(t('sug.agentFallback', { reason: outcome.reason ?? '' }));
          const results = await runBatch(selectedShop, date);
          setBatchResults(results);
          message.success(t('sug.generated', { count: results.length }));
        } else {
          setBatchResults(outcome.results);
          message.success(t('sug.generatedAgent', { count: outcome.results.length }));
        }
      } else {
        const results = await runBatch(selectedShop, date);
        setBatchResults(results);
        message.success(t('sug.generated', { count: results.length }));
      }
      setQtyEdits({});
      setReadiness(undefined);
    } catch (error) {
      message.error(t('sug.runFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setLoading(false);
    }
  };

  const handleRun = async () => {
    if (!selectedShop) {
      message.warning(t('sug.warnSelectShop'));
      return;
    }
    // Fully automatic: no config gate. Unconfigured store/SKU simply fall back to
    // system defaults; staff can fine-tune via 补货参数配置 at any time.
    await doRun();
  };

  const handleConfigSaved = () => {
    setConfigOpen(false);
    message.success(t('cfg.saved'));
  };

  // ---- Staff adjustment of order quantities --------------------------------
  const dirtyAdjust = useMemo(
    () => batchResults.filter((r) => qtyEdits[r.sku] !== undefined && qtyEdits[r.sku] !== finalOf(r)).length,
    [batchResults, qtyEdits],
  );

  const saveAdjustments = () => {
    if (dirtyAdjust === 0) {
      message.info(t('sug.adjustNone'));
      return;
    }
    setReasonOpen(true);
  };

  const confirmAdjustments = async () => {
    if (!reasonCode) {
      message.warning(t('sug.reasonRequired'));
      return;
    }
    const runId = batchResults.find((r) => r.run_id)?.run_id;
    const items = batchResults
      .filter((r) => qtyEdits[r.sku] !== undefined && qtyEdits[r.sku] !== finalOf(r))
      .map((r) => ({
        sku: r.sku,
        final_qty: qtyEdits[r.sku],
        reason_code: reasonCode,
        reason_text: reasonText.trim() || undefined,
        event_id: crypto.randomUUID(),
      }));
    if (items.length === 0) {
      message.info(t('sug.adjustNone'));
      return;
    }
    if (!runId) {
      message.error(t('sug.liveBackendRequired'));
      return;
    }
    setAdjusting(true);
    try {
      const res = await adjustRun(runId, items, lang === 'zh' ? 'zh-CN' : 'en-US');
      setBatchResults(res.results);
      setQtyEdits({});
      setReasonOpen(false);
      setReasonCode(undefined);
      setReasonText('');
      const gate = await fetchRunSubmissionReadiness(runId);
      setReadiness(gate);
      message.success(t('sug.adjustSavedAttribution', { n: res.changed }));
      if (res.case_ids?.length === 1) {
        navigate(`/attribution/${res.case_ids[0]}`);
      } else if (res.job_id) {
        navigate(`/attribution?job_id=${encodeURIComponent(res.job_id)}`);
      }
    } catch (error) {
      message.error(t('cfg.saveFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setAdjusting(false);
    }
  };

  const handleSubmitRun = async () => {
    const runId = batchResults.find((r) => r.run_id)?.run_id;
    if (!runId) {
      message.error(t('sug.liveBackendRequired'));
      return;
    }
    setSubmittingRun(true);
    try {
      const gate = await fetchRunSubmissionReadiness(runId);
      setReadiness(gate);
      if (!gate.ready) {
        const firstCase = gate.blockers.find((item) => item.case_id)?.case_id;
        Modal.warning({
          title: t('sug.submitBlockedTitle'),
          content: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>{t('sug.submitBlockedBody', { approved: gate.approved_count, modified: gate.modified_count })}</Text>
              {gate.blockers.map((blocker) => (
                <Alert
                  key={`${blocker.sku}-${blocker.code}`}
                  type="warning"
                  showIcon
                  message={`${blocker.sku} · ${blocker.message}`}
                />
              ))}
            </Space>
          ),
          okText: firstCase ? t('sug.openBlockingCase') : t('sug.ok'),
          onOk: () => {
            if (firstCase) navigate(`/attribution/${firstCase}`);
          },
        });
        return;
      }
      Modal.confirm({
        title: t('sug.submitConfirmTitle'),
        content: t('sug.submitConfirmBody'),
        okText: t('sug.submitFinal'),
        cancelText: t('sug.cancel'),
        onOk: async () => {
          const submitted = await submitRun(runId, gate.run_version);
          setReadiness({ ...gate, status: submitted.status, ready: false, run_version: submitted.run_version });
          message.success(t('sug.submittedLocked'));
        },
      });
    } catch (error) {
      message.error(t('sug.submitFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setSubmittingRun(false);
    }
  };

  // ---- Inventory table columns ---------------------------------------------
  const invNumberCol = (field: keyof InventoryRow, title: ReactNode): ColumnsType<InventoryRow>[number] => ({
    title, dataIndex: field, align: 'right',
    render: (_v, row) => (
      <InputNumber
        size="small"
        style={{ width: 84 }}
        min={0}
        precision={0}
        value={row[field] as number}
        onChange={(v) => editInv(row.goods_code, field, v)}
      />
    ),
  });

  const inventoryColumns: ColumnsType<InventoryRow> = [
    {
      title: 'SKU', dataIndex: 'goods_code', fixed: 'left',
      render: (gc: string, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{gc}</Text>
          <Text type="secondary">{row.goods_name}</Text>
        </Space>
      ),
    },
    invNumberCol('on_hand', t('sug.colOnHand')),
    invNumberCol('in_transit', t('sug.colInTransit')),
    invNumberCol('reserved', t('sug.colReserved')),
    invNumberCol('expiring', t('sug.colExpiring')),
    invNumberCol('days_to_expiry', <Tooltip title={t('sug.colDaysToExpiryHint')}>{t('sug.colDaysToExpiry')}</Tooltip>),
    invNumberCol('recent_zero_days', t('sug.colZeroDays')),
    { title: t('sug.colAvailable'), dataIndex: 'available', align: 'right', render: (v: number) => <Text strong>{v}</Text> },
    {
      title: t('sug.colSource'), dataIndex: 'source', align: 'center',
      render: (src: string) => src === 'override'
        ? <Tag color="geekblue">{t('sug.override')}</Tag>
        : <Tag>{t('sug.synthetic')}</Tag>,
    },
    {
      title: t('sug.colOps'), key: 'ops', align: 'center', fixed: 'right',
      render: (_v, row) => (
        <Space size={4}>
          <Button
            size="small" type="link"
            loading={savingRow === row.goods_code}
            disabled={!invDirty.has(row.goods_code)}
            onClick={() => void saveInvRow(row)}
          >
            {t('sug.saveRow')}
          </Button>
          {row.source === 'override' && (
            <Popconfirm title={t('sug.resetConfirm')} onConfirm={() => void resetInvRow(row)} okText={t('sug.ok')} cancelText={t('sug.cancel')}>
              <Button size="small" type="link" danger>{t('sug.resetRow')}</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  // ---- Result table columns ------------------------------------------------
  const columns: ColumnsType<ReplenishmentResult> = [
    {
      title: 'SKU', dataIndex: 'sku', fixed: 'left',
      render: (sku: string, record: ReplenishmentResult) => (
        <Space direction="vertical" size={0}>
          <Text strong>{sku}</Text>
          <Text type="secondary">{skuNameMap.get(sku) ?? record.sku_name ?? t('sug.unknownSku')}</Text>
        </Space>
      ),
    },
    {
      title: t('sug.colScenario'), dataIndex: 'scenario',
      render: (scenario: Scenario) => <Tag color={scenarioColors[scenario]}>{scenarioLabel(scenario)}</Tag>,
      filters: scenarioKeys.map((value) => ({ value, text: scenarioLabel(value) })),
      onFilter: (value, record) => record.scenario === value,
    },
    { title: t('sug.colPosition'), dataIndex: 'position', align: 'right', render: (v?: number) => v ?? '-' },
    {
      title: <Tooltip title={t('sug.colReorderHint')}>{t('sug.colReorder')}</Tooltip>,
      dataIndex: 'reorder_point', align: 'right', render: (v?: number) => v ?? '-',
    },
    {
      title: <Tooltip title={t('sug.colOrderUpHint')}>{t('sug.colOrderUp')}</Tooltip>,
      dataIndex: 'order_up_to', align: 'right', render: (v?: number) => v ?? '-',
    },
    {
      title: t('sug.colTriggered'), key: 'triggered', align: 'center',
      render: (_v, r) => (r.triggered ?? r.trigger)
        ? <Tag color="volcano">{t('sug.triggered')}</Tag>
        : <Tag color="default">{t('sug.skipped')}</Tag>,
      filters: [{ value: true, text: t('sug.triggered') }, { value: false, text: t('sug.skipped') }],
      onFilter: (value, r) => (r.triggered ?? r.trigger ?? false) === value,
    },
    { title: t('sug.colChosen'), dataIndex: 'chosen_qty', align: 'right', render: (value: number) => <Text type="secondary">{value}</Text> },
    {
      title: t('sug.colFinalQty'), key: 'final_qty', align: 'right', fixed: 'right',
      render: (_v, r) => {
        const val = qtyEdits[r.sku] ?? finalOf(r);
        const dirty = qtyEdits[r.sku] !== undefined && qtyEdits[r.sku] !== finalOf(r);
        return (
          <InputNumber
            size="small"
            style={{ width: 88 }}
            min={0}
            precision={0}
            value={val}
            disabled={readiness?.status === 'SUBMITTED_LOCKED'}
            status={dirty ? 'warning' : undefined}
            onChange={(v) => setQtyEdits((e) => ({ ...e, [r.sku]: Math.max(0, Math.round(Number(v ?? 0))) }))}
          />
        );
      },
    },
    { title: t('sug.colException'), dataIndex: 'exception', align: 'center', render: (exception: boolean) => <Tag color={exception ? 'red' : 'green'}>{exception ? t('sug.needReview') : t('sug.normal')}</Tag> },
  ];

  const scheduleSample = batchResults[0];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card>
        <Space className="toolbar" align="start" wrap>
          <div>
            <Title level={3}>{t('sug.title')}</Title>
            <Text type="secondary">{t('sug.subtitle')}</Text>
            <div style={{ marginTop: 6 }}>
              <Tag color={engineMode === 'agent' ? 'geekblue' : 'blue'}>
                {engineMode === 'agent' ? t('sug.agentTag') : t('sug.engineTag')}
              </Tag>
            </div>
          </div>
          <Space direction="vertical" size={8} align="start">
            <Space wrap align="center">
              <DatePicker onChange={(_, value) => setDate(Array.isArray(value) ? value[0] : value || today())} placeholder={date} />
              <Button icon={<SettingOutlined />} disabled={!selectedShop} onClick={() => setConfigOpen(true)}>{t('sug.configure')}</Button>
              <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={handleRun}>{t('sug.generate')}</Button>
            </Space>
          </Space>
        </Space>
      </Card>

      {!selectedShop && (
        <Alert type="info" showIcon message={t('sug.warnSelectShop')} />
      )}

      {/* Auto-loaded current inventory (staff-editable) */}
      {selectedShop && (
        <Card
          title={t('sug.inventoryTitle')}
          extra={<Button size="small" icon={<ReloadOutlined />} loading={invLoading} onClick={() => void loadInventory()}>{t('sug.refresh')}</Button>}
        >
          <Text type="secondary">{t('sug.inventorySubtitle')}</Text>
          <Table
            style={{ marginTop: 12 }}
            size="small"
            rowKey="goods_code"
            loading={invLoading}
            columns={inventoryColumns}
            dataSource={inv}
            scroll={{ x: 900 }}
            locale={{ emptyText: <Empty description={t('sug.invEmpty')} /> }}
            pagination={{ pageSize: 8 }}
          />
        </Card>
      )}

      {scheduleSample && (
        <Alert
          type="success"
          showIcon
          message={t('sug.scheduleInfo', {
            apply: scheduleSample.apply_date ?? '-',
            arrival: scheduleSample.arrival_date ?? '-',
            shelf: scheduleSample.shelf_date ?? '-',
          })}
        />
      )}

      <Card
        title={t('sug.cardResults')}
        extra={
          <Space>
            {selectedSku ? <Tag color="blue">{t('sug.filtered')}</Tag> : <Tag>{t('sug.allSku')}</Tag>}
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={adjusting}
              disabled={dirtyAdjust === 0 || readiness?.status === 'SUBMITTED_LOCKED'}
              onClick={saveAdjustments}
            >
                {t('sug.saveDraftAttribution', { n: dirtyAdjust })}
            </Button>
              <Button
                icon={<FileDoneOutlined />}
                loading={submittingRun}
                disabled={!batchResults.length || dirtyAdjust > 0 || readiness?.status === 'SUBMITTED_LOCKED'}
                onClick={() => void handleSubmitRun()}
              >
                {t('sug.submitFinal')}
              </Button>
          </Space>
        }
      >
        <Table
          rowKey="trace_id"
          loading={loading}
          columns={columns}
          dataSource={visibleResults}
          scroll={{ x: 1000 }}
          locale={{ emptyText: <Empty description={t('sug.empty')} /> }}
          expandable={{
            expandedRowRender: (record) => (
              <ReplenishmentResultDetails record={record} />
            ),
          }}
          pagination={{ pageSize: 8 }}
        />
      </Card>

      {readiness && (
        <Alert
          showIcon
          type={readiness.status === 'READY_TO_SUBMIT' ? 'success' : readiness.status === 'SUBMITTED_LOCKED' ? 'info' : 'warning'}
          message={(
            <Space wrap>
              <RunGateStatusTag status={readiness.status} lang={lang} />
              <Text>{t('sug.gateProgress', { approved: readiness.approved_count, modified: readiness.modified_count })}</Text>
            </Space>
          )}
          description={readiness.blockers.length ? t('sug.gateBlocked', { count: readiness.blockers.length }) : undefined}
        />
      )}

      <Drawer
        title={t('cfg.drawerTitle')}
        width={760}
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        destroyOnClose
      >
        {selectedShop && (
          <ConfigPanel
            shopCode={selectedShop}
            shopName={selectedShopName}
            goodsCode={selectedSku}
            skus={skus}
            onSaved={handleConfigSaved}
          />
        )}
      </Drawer>

      <Modal
        open={reasonOpen}
        title={t('sug.reasonTitle')}
        okText={t('sug.saveDraftAttribution', { n: dirtyAdjust })}
        cancelText={t('sug.cancel')}
        confirmLoading={adjusting}
        onCancel={() => setReasonOpen(false)}
        onOk={() => void confirmAdjustments()}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert showIcon type="info" message={t('sug.reasonGateNotice')} />
          <Select
            style={{ width: '100%' }}
            placeholder={t('sug.reasonCode')}
            value={reasonCode}
            onChange={setReasonCode}
            options={[
              { value: 'DEMAND_CHANGE', label: t('sug.reasonDemand') },
              { value: 'SEASONAL', label: t('sug.reasonSeasonal') },
              { value: 'SUBSTITUTION', label: t('sug.reasonSubstitution') },
              { value: 'INVENTORY_CONSTRAINT', label: t('sug.reasonInventory') },
              { value: 'OTHER', label: t('sug.reasonOther') },
            ]}
          />
          <Input.TextArea
            rows={4}
            value={reasonText}
            placeholder={t('sug.reasonText')}
            onChange={(event) => setReasonText(event.target.value)}
          />
          <Text type="secondary">{t('sug.reasonUntrusted')}</Text>
        </Space>
      </Modal>
    </Space>
  );
}
