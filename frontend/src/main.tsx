import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./style.css";
import "./visual-revision.css";
import "./living-scene.css";
import "./conversation-drawer.css";
import "./memory.css";
import "./select.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      select={{ className: "qiyu-select", classNames: { popup: { root: "qiyu-select-menu" } } }}
      theme={{
        token: {
          colorPrimary: "#356451",
          colorText: "#253b31",
          fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
          borderRadius: 10,
          controlHeight: 40,
        },
        components: {
          Button: {
            defaultBg: "var(--button-bg)",
            defaultBorderColor: "var(--button-border)",
            defaultHoverBg: "var(--button-hover-bg)",
            defaultHoverBorderColor: "var(--button-hover-border)",
            defaultHoverColor: "#314b37",
            defaultActiveBg: "var(--button-active-bg)",
            defaultActiveBorderColor: "#7e906b",
            defaultActiveColor: "#294632",
            defaultShadow: "none",
          },
          Modal: { contentBg: "#fcfcf8" },
          Select: { optionSelectedBg: "#e6eee6" },
          DatePicker: {
            colorBgContainer: "#e9e0cb",
            colorBgElevated: "#f2ead6",
            colorBorder: "#bfb193",
            colorPrimary: "#48684f",
            colorLink: "#48684f",
            colorLinkHover: "#34543d",
            colorLinkActive: "#34543d",
            colorText: "#354835",
            colorTextPlaceholder: "#666c58",
            colorTextDisabled: "#8b8878",
            colorTextQuaternary: "#7d786b",
            colorSplit: "#d7cbb0",
            hoverBorderColor: "#7e906b",
            activeBorderColor: "#7e906b",
            activeShadow: "0 0 0 2px #7e906b30",
            cellHoverBg: "#e5dcc4",
            cellBgDisabled: "transparent",
            cellHeight: 30,
            cellWidth: 33,
            paddingXXS: 2,
            textHeight: 40,
          },
        },
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
