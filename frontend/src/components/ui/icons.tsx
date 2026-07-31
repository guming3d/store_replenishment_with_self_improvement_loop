import type { CSSProperties } from 'react';
import {
  IconHelpCircle,
  IconDeviceFloppy,
  IconRefresh,
  IconArrowBackUp,
  IconFileCheck,
  IconSearch,
  IconSettings,
  IconCircleCheck,
  IconCircleX,
  IconAlertCircle,
  IconArrowDown,
  IconArrowUp,
  IconPlug,
  IconBulb,
  IconTrash,
  IconFileExport,
  IconEye,
  IconRobot,
  IconBolt,
  IconClipboardCheck,
  IconGitBranch,
  IconCalculator,
  IconDatabase,
  IconDeviceDesktop,
  IconEdit,
  IconLock,
  IconPlayerPlay,
  IconBook,
  IconShieldCheck,
  IconSend,
  IconBuildingStore,
  IconUsers,
  IconArrowLeft,
  IconDownload,
  IconBan,
  IconPlus,
  type IconProps as TablerIconProps,
} from '@tabler/icons-react';

interface AliasProps {
  size?: number;
  color?: string;
  className?: string;
  style?: CSSProperties;
  spin?: boolean;
  'aria-hidden'?: boolean | 'true' | 'false';
}

type TablerIcon = (props: TablerIconProps) => React.ReactNode;

function alias(Icon: TablerIcon, defaultSize = 16) {
  return function AliasedIcon({ size, spin, className, ...rest }: AliasProps) {
    const cls = [spin ? 'x-spin' : null, className].filter(Boolean).join(' ') || undefined;
    return <Icon size={size ?? defaultSize} className={cls} {...(rest as TablerIconProps)} />;
  };
}

export const QuestionCircleOutlined = alias(IconHelpCircle);
export const SaveOutlined = alias(IconDeviceFloppy);
export const ReloadOutlined = alias(IconRefresh);
export const UndoOutlined = alias(IconArrowBackUp);
export const FileDoneOutlined = alias(IconFileCheck);
export const SearchOutlined = alias(IconSearch);
export const SettingOutlined = alias(IconSettings);
export const CheckCircleOutlined = alias(IconCircleCheck);
export const CloseCircleOutlined = alias(IconCircleX);
export const ExclamationCircleOutlined = alias(IconAlertCircle);
export const SyncOutlined = alias(IconRefresh);
export const ArrowDownOutlined = alias(IconArrowDown);
export const ArrowUpOutlined = alias(IconArrowUp);
export const ApiOutlined = alias(IconPlug);
export const BulbOutlined = alias(IconBulb);
export const DeleteOutlined = alias(IconTrash);
export const ExportOutlined = alias(IconFileExport);
export const EyeOutlined = alias(IconEye);
export const RobotOutlined = alias(IconRobot);
export const ThunderboltOutlined = alias(IconBolt);
export const AuditOutlined = alias(IconClipboardCheck, 18);
export const BranchesOutlined = alias(IconGitBranch, 18);
export const CalculatorOutlined = alias(IconCalculator, 18);
export const DatabaseOutlined = alias(IconDatabase, 18);
export const DesktopOutlined = alias(IconDeviceDesktop, 18);
export const EditOutlined = alias(IconEdit);
export const LockOutlined = alias(IconLock, 18);
export const PlayCircleOutlined = alias(IconPlayerPlay);
export const ReadOutlined = alias(IconBook);
export const SafetyCertificateOutlined = alias(IconShieldCheck, 18);
export const SendOutlined = alias(IconSend, 18);
export const ShopOutlined = alias(IconBuildingStore, 18);
export const TeamOutlined = alias(IconUsers, 18);
export const ArrowLeftOutlined = alias(IconArrowLeft);
export const DownloadOutlined = alias(IconDownload);
export const StopOutlined = alias(IconBan);
export const PlusOutlined = alias(IconPlus);
