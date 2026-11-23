import type { IconName } from "../ui/Icon";

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "Overview",
    icon: "overview",
    description: "Platform health and recent activity",
  },
  {
    to: "/models",
    label: "Model Manager",
    icon: "models",
    description: "Discover, inspect and verify local models",
  },
  {
    to: "/hardware",
    label: "Hardware Monitor",
    icon: "hardware",
    description: "CPU, memory, accelerators and backends",
  },
  {
    to: "/optimizer",
    label: "Optimizer",
    icon: "optimizer",
    description: "Search spaces and live optimization trials",
  },
  {
    to: "/evaluation",
    label: "Evaluation Center",
    icon: "evaluation",
    description: "Suites, runs, reports and comparisons",
  },
  {
    to: "/experiments",
    label: "Experiments",
    icon: "experiments",
    description: "Reproducibility records and artifacts",
  },
  {
    to: "/plugins",
    label: "Plugins",
    icon: "plugins",
    description: "Installed extensions and permissions",
  },
  {
    to: "/exports",
    label: "Export Center",
    icon: "exports",
    description: "Export bundles produced by the CLI",
  },
  {
    to: "/security",
    label: "Security",
    icon: "security",
    description: "Policy, audit trail, findings and SBOM",
  },
  {
    to: "/settings",
    label: "Settings",
    icon: "settings",
    description: "Redacted configuration and API access",
  },
];

export function findNavItem(pathname: string): NavItem | undefined {
  if (pathname === "/") return NAV_ITEMS[0];
  return NAV_ITEMS.find(
    (item) => item.to !== "/" && (pathname === item.to || pathname.startsWith(`${item.to}/`)),
  );
}
