import type { CSSProperties, ReactNode, MouseEventHandler } from 'react';
import { Heading, Text as RText, Link as RLink } from '@radix-ui/themes';

type TextType = 'secondary' | 'success' | 'warning' | 'danger';

const typeColor = (type?: TextType) => {
  switch (type) {
    case 'secondary':
      return 'gray';
    case 'success':
      return 'green';
    case 'warning':
      return 'amber';
    case 'danger':
      return 'red';
    default:
      return undefined;
  }
};

interface TextProps {
  children?: ReactNode;
  type?: TextType;
  strong?: boolean;
  code?: boolean;
  className?: string;
  style?: CSSProperties;
  title?: string;
}

export function Text({ children, type, strong, className, style, title }: TextProps) {
  const color = typeColor(type);
  return (
    <RText
      className={className}
      style={style}
      title={title}
      color={color as any}
      weight={strong ? 'bold' : undefined}
    >
      {children}
    </RText>
  );
}

interface ParagraphProps extends TextProps {
  ellipsis?: boolean;
}

export function Paragraph({ children, type, strong, className, style }: ParagraphProps) {
  const color = typeColor(type);
  return (
    <RText
      as="p"
      className={className}
      style={{ margin: '0 0 1em', ...style }}
      color={color as any}
      weight={strong ? 'bold' : undefined}
    >
      {children}
    </RText>
  );
}

const HEADING_SIZE: Record<number, '4' | '5' | '6' | '7' | '8'> = {
  1: '8',
  2: '7',
  3: '6',
  4: '5',
  5: '4',
};

interface TitleProps {
  children?: ReactNode;
  level?: 1 | 2 | 3 | 4 | 5;
  className?: string;
  style?: CSSProperties;
}

export function Title({ children, level = 1, className, style }: TitleProps) {
  const as = (`h${level}`) as 'h1' | 'h2' | 'h3' | 'h4' | 'h5';
  return (
    <Heading as={as} size={HEADING_SIZE[level]} className={className} style={style}>
      {children}
    </Heading>
  );
}

interface LinkProps {
  children?: ReactNode;
  href?: string;
  onClick?: MouseEventHandler;
  className?: string;
  style?: CSSProperties;
  type?: TextType;
}

export function Link({ children, href, onClick, className, style, type }: LinkProps) {
  const color = typeColor(type);
  return (
    <RLink
      href={href}
      onClick={onClick}
      className={className}
      style={{ cursor: 'pointer', ...style }}
      color={color as any}
    >
      {children}
    </RLink>
  );
}

export const Typography = { Text, Title, Paragraph, Link };
export default Typography;
