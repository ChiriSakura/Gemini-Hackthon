/**
 * Inline SVG Line Icon Set for StageState.
 *
 * Feature & Use Cases:
 * - One consistent family of 24x24 line icons (stroke 1.75, `currentColor`) used
 *   by the top bar, overflow menu, stage transport, QA line, save tree and
 *   input bar, so the interface needs no emoji and no icon dependency.
 * - Icons are decorative (`aria-hidden`); buttons that show only an icon carry
 *   their own `aria-label` and `title`.
 */
import React from 'react';

interface IconProps {
  size?: number;
  className?: string;
}

function Svg({
  size = 18,
  className,
  children,
}: IconProps & { children: React.ReactNode }): React.ReactElement {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {children}
    </svg>
  );
}

export const IconMore = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Svg>
);

export const IconPlay = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7 4.5v15l12-7.5z" />
  </Svg>
);

export const IconStop = (p: IconProps) => (
  <Svg {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </Svg>
);

export const IconStage = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" />
  </Svg>
);

export const IconFilm = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
  </Svg>
);

export const IconVolume = (p: IconProps) => (
  <Svg {...p}>
    <path d="M11 5L6 9H3v6h3l5 4z" />
    <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
  </Svg>
);

export const IconVolumeOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M11 5L6 9H3v6h3l5 4z" />
    <path d="M16 9.5l5 5M21 9.5l-5 5" />
  </Svg>
);

export const IconMic = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </Svg>
);

export const IconCamera = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" />
    <circle cx="12" cy="13" r="3.5" />
  </Svg>
);

export const IconClapper = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 11h16v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8z" />
    <path d="M3.5 7.6l15.7-4 .8 3.3-15.7 4z" />
    <path d="M8.2 6.4l1.4 3M13.2 5.1l1.4 3" />
  </Svg>
);

export const IconCompare = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M12 4v16" />
  </Svg>
);

export const IconGrid = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
  </Svg>
);

export const IconChart = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 20h18M6 20V11M12 20V5M18 20v-6" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </Svg>
);

export const IconSync = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 0 0-14.3-4.3L4 8M4 4v4h4" />
    <path d="M4 13a8 8 0 0 0 14.3 4.3L20 16M20 20v-4h-4" />
  </Svg>
);

export const IconScissors = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="6" cy="6.5" r="2.5" />
    <circle cx="6" cy="17.5" r="2.5" />
    <path d="M8.2 8L20 18M8.2 16L20 6" />
  </Svg>
);

export const IconLayers = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l9 4.5-9 4.5-9-4.5z" />
    <path d="M3 12l9 4.5 9-4.5M3 16.5l9 4.5 9-4.5" />
  </Svg>
);

export const IconEye = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="3" />
  </Svg>
);

export const IconReset = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5" />
  </Svg>
);

export const IconHelp = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1 1-1.1 1.8M12 17h.01" />
  </Svg>
);

export const IconChevronUp = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 15l6-6 6 6" />
  </Svg>
);

export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 9l6 6 6-6" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
);

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
);

export const IconList = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />
  </Svg>
);

export const IconHalo = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <circle cx="12" cy="12" r="8.5" strokeDasharray="3 3" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4l9 16H3zM12 10v4M12 17h.01" />
  </Svg>
);

export const IconPlug = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5" />
  </Svg>
);
