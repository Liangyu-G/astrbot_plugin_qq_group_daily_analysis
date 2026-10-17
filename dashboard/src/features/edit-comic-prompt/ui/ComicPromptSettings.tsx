import { Alert, Button, Input, Popconfirm, Select, Space, Typography, theme } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { comicPromptTemplates, useComicPromptEditor } from "../model/useComicPromptEditor";

interface ComicPromptSettingsProps {
  value: unknown;
  onChange: (value: unknown) => void;
  idPrefix: string;
}

/** 漫画全局风格选择、内置预览及自定义编辑。 */
export function ComicPromptSettings({ value, onChange, idPrefix }: ComicPromptSettingsProps) {
  const { token } = theme.useToken();
  const editor = useComicPromptEditor(value, onChange);
  return (
    <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
      <div style={{ display: "grid", gap: 6 }}>
        <label htmlFor={`${idPrefix}-template`}>默认漫画提示词模板</label>
        <Select
          id={`${idPrefix}-template`}
          value={editor.selection}
          onChange={editor.selectTemplate}
          style={{ width: "100%" }}
          options={[
            ...comicPromptTemplates.map(({ id, name }) => ({ value: id, label: name })),
            { value: "custom", label: "自定义" },
          ]}
        />
      </div>
      {editor.preset ? (
        <>
          <Alert type="info" showIcon message={editor.preset.description} />
          <Space wrap>
            <Popconfirm
              title="替换自定义提示词？"
              description="将当前内置模板复制到自定义编辑区，替换原有自定义内容。"
              disabled={!editor.custom.trim() || editor.custom === editor.preset.prompt}
              onConfirm={editor.copyPreset}
              okText="复制并替换"
              cancelText="保留原文"
            >
              <Button
                icon={<CopyOutlined />}
                onClick={() => {
                  if (!editor.custom.trim() || editor.custom === editor.preset?.prompt) editor.copyPreset();
                }}
              >
                复制为自定义
              </Button>
            </Popconfirm>
            <Typography.Text type="secondary">切换风格会保留自定义文本。</Typography.Text>
          </Space>
          <details>
            <summary style={{ cursor: "pointer", padding: "8px 0", color: token.colorPrimary }}>
              查看完整内置提示词
            </summary>
            <Input.TextArea
              aria-label={`${editor.preset.name}提示词预览`}
              value={editor.preset.prompt}
              readOnly
              autoSize={{ minRows: 8, maxRows: 16 }}
              style={{ marginTop: 8, lineHeight: 1.7 }}
            />
          </details>
        </>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          <label htmlFor={`${idPrefix}-custom`}>自定义漫画场景分析提示词</label>
          <Input.TextArea
            id={`${idPrefix}-custom`}
            value={editor.custom}
            onChange={(event) => editor.editCustom(event.target.value)}
            autoSize={{ minRows: 10, maxRows: 22 }}
            placeholder="留空使用原版漫画提示词"
            aria-describedby={`${idPrefix}-hint`}
            style={{ lineHeight: 1.7 }}
          />
          <Typography.Text id={`${idPrefix}-hint`} type="secondary">
            {"保留 ${topic_count} 和 ${chat_content}，输出需包含完整的 scene 字符串。留空时使用原版漫画。"}
          </Typography.Text>
        </div>
      )}
      <Typography.Text type="secondary">
        角色方案中的专属提示词优先。要跟随这里的风格，请将角色的「漫画场景分析提示词」留空。
      </Typography.Text>
    </div>
  );
}
