import React, { useEffect, useMemo, useState } from "react";
import { Drawer } from "antd";
import { IconButton } from "@agentscope-ai/design";
import { SparkOperateRightLine, SparkRefreshLine } from "@agentscope-ai/icons";
import { useTranslation } from "react-i18next";
import styles from "./index.module.less";

interface DesktopMonitorDrawerProps {
  open: boolean;
  onClose: () => void;
}

const DESKTOP_MONITOR_WIDTH = "clamp(360px, 38vw, 560px)";

function getDesktopMonitorUrl(): string {
  const configuredUrl = (import.meta as any).env?.VITE_DESKTOP_MONITOR_URL;
  if (typeof configuredUrl === "string" && configuredUrl.trim()) {
    return configuredUrl.trim();
  }

  if (typeof window === "undefined") {
    return "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale";
  }

  const url = new URL(window.location.href);
  url.port = "6080";
  url.pathname = "/vnc.html";
  url.search = "autoconnect=true&resize=scale";
  url.hash = "";
  return url.toString();
}

const DesktopMonitorDrawer: React.FC<DesktopMonitorDrawerProps> = ({
  open,
  onClose,
}) => {
  const { t } = useTranslation();
  const desktopUrl = useMemo(() => getDesktopMonitorUrl(), []);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (typeof document === "undefined") return;

    if (!open) {
      document.body.classList.remove("qwenpaw-desktop-monitor-open");
      document.body.style.removeProperty("--qwenpaw-desktop-monitor-width");
      return;
    }

    document.body.classList.add("qwenpaw-desktop-monitor-open");
    document.body.style.setProperty(
      "--qwenpaw-desktop-monitor-width",
      DESKTOP_MONITOR_WIDTH,
    );

    return () => {
      document.body.classList.remove("qwenpaw-desktop-monitor-open");
      document.body.style.removeProperty("--qwenpaw-desktop-monitor-width");
    };
  }, [open]);

  return (
    <Drawer
      className={styles.drawer}
      open={open}
      onClose={onClose}
      placement="right"
      width={DESKTOP_MONITOR_WIDTH}
      mask={false}
      autoFocus={false}
      keyboard={false}
      closable={false}
      title={null}
      destroyOnHidden
      styles={{ body: { padding: 0 } }}
    >
      <div className={styles.header}>
        <div className={styles.headerText}>
          <span className={styles.headerTitle}>
            {t("chat.desktopMonitorTitle", "Desktop monitor")}
          </span>
          <span className={styles.headerSubtitle}>
            {t("chat.desktopMonitorSubtitle", "Live system screen")}
          </span>
        </div>
        <IconButton
          bordered={false}
          icon={<SparkRefreshLine />}
          onClick={() => setReloadKey((key) => key + 1)}
        />
        <IconButton
          bordered={false}
          icon={<SparkOperateRightLine />}
          onClick={onClose}
        />
      </div>

      <div className={styles.monitorFrameWrap}>
        <iframe
          key={reloadKey}
          className={styles.monitorFrame}
          src={desktopUrl}
          title={t("chat.desktopMonitorTitle", "Desktop monitor")}
          allow="clipboard-read; clipboard-write"
        />
      </div>
    </Drawer>
  );
};

export default DesktopMonitorDrawer;
