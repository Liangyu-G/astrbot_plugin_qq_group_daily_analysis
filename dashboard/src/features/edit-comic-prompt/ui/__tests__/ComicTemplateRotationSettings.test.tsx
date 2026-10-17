import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComicTemplateRotationSettings } from "../ComicTemplateRotationSettings";
import { fetchComicRotationStatus, resetComicRotation } from "../../api/comicRotationApi";
import { fetchDistinctGroups } from "../../../../entities/group/api/groupApi";
import { fetchComicTemplatePreview } from "../../api/comicTemplateApi";
import { COMIC_ROTATION_MODES, type ComicTemplateRotationStatus } from "../../model/comicTemplateRotation";

vi.mock("../../api/comicRotationApi", () => ({ fetchComicRotationStatus: vi.fn(), resetComicRotation: vi.fn() }));
vi.mock("../../api/comicTemplateApi", () => ({ fetchComicTemplatePreview: vi.fn() }));
vi.mock("../../../../entities/group/api/groupApi", () => ({ fetchDistinctGroups: vi.fn() }));
const nativeGetComputedStyle = window.getComputedStyle.bind(window);
const status: ComicTemplateRotationStatus = {
  mode: "sequential", scope: "global", scope_key: "global", candidates: ["classic", "pixel_rpg"], current_template_id: "classic", current_template_name: "原版漫画", next_template_id: "pixel_rpg", next_template_name: "像素 RPG 每日冒险日志", date: "2026-10-17", initialized: true, warning: null,
};
const saved = { comic_template_mode: "sequential", comic_template_scope: "global", comic_template_candidates: ["classic", "pixel_rpg"] };
const openPrompts = vi.fn();

function Editor({ initial = saved, dirty = false }: { initial?: Record<string, unknown>; dirty?: boolean }) {
  const [value, setValue] = useState(initial);
  return <>
    <ComicTemplateRotationSettings value={value} savedValue={initial} dirty={dirty || value !== initial} customPrompt="我的自定义提示词" onPatch={(patch) => setValue((previous) => ({ ...previous, ...patch }))} onOpenPromptSettings={openPrompts} />
    <output data-testid="comic-draft">{JSON.stringify(value)}</output>
  </>;
}

describe("漫画模板自动切换设置", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => nativeGetComputedStyle(element));
    vi.mocked(fetchComicRotationStatus).mockResolvedValue(status);
    vi.mocked(fetchDistinctGroups).mockResolvedValue([]);
    vi.mocked(fetchComicTemplatePreview).mockResolvedValue("data:image/webp;base64,YWJj");
    vi.mocked(resetComicRotation).mockResolvedValue({ ...status, initialized: false });
  });
  afterEach(() => vi.restoreAllMocks());

  it("提供六种模式，固定模式沿用原默认模板并保留候选", async () => {
    render(<Editor initial={{ ...saved, comic_template_mode: "fixed" }} />);
    fireEvent.mouseDown(screen.getByLabelText("切换模式"));
    for (const mode of COMIC_ROTATION_MODES) expect(screen.getAllByText(mode.label).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("每次随机"));
    expect(screen.getByTestId("comic-draft").textContent).toContain('"comic_template_mode":"random"');
    expect(screen.getByRole("list", { name: "漫画模板候选顺序" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑自定义提示词" }));
    expect(openPrompts).toHaveBeenCalled();
    await waitFor(() => expect(fetchComicRotationStatus).toHaveBeenCalledTimes(1));
    expect(resetComicRotation).not.toHaveBeenCalled();
  });

  it("上下移动和移除改变候选 ID，显示已保存状态并锁定草稿重置", async () => {
    render(<Editor />);
    await waitFor(() => expect(screen.getByRole("button", { name: /重置进度/ })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "下移原版漫画" }));
    expect(JSON.parse(screen.getByTestId("comic-draft").textContent!).comic_template_candidates).toEqual(["pixel_rpg", "classic"]);
    expect(screen.getByText("漫画配置有未保存修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重置进度/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "移除原版漫画" }));
    expect(JSON.parse(screen.getByTestId("comic-draft").textContent!).comic_template_candidates).toEqual(["pixel_rpg"]);
    expect(screen.getByText("服务器日期")).toBeInTheDocument();
    expect(resetComicRotation).not.toHaveBeenCalled();
  });

  it("画廊添加到候选末尾，预览不推进且保留模板模式", async () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole("button", { name: /浏览模板画廊/ }));
    const gallery = screen.getByRole("dialog", { name: "漫画模板视觉样式画廊" });
    expect(within(gallery).getByRole("button", { name: "原版漫画已加入候选" })).toBeDisabled();
    fireEvent.click(within(gallery).getByRole("button", { name: "添加候选黑色电影侦探风" }));
    const draft = JSON.parse(screen.getByTestId("comic-draft").textContent!);
    expect(draft.comic_template_candidates).toEqual(["classic", "pixel_rpg", "noir_detective"]);
    expect(draft.comic_template_mode).toBe("sequential");
    fireEvent.click(await within(gallery).findByRole("button", { name: "放大黑色电影侦探风示例图" }));
    // rc-dialog 在测试环境复用 test-id，按弹窗实际内容区分叠加的两个对话框。
    const preview = screen.getAllByRole("dialog").find((dialog) => dialog.textContent?.includes("黑色电影侦探风 · 风格示例"))!;
    expect(await within(preview).findByRole("img", { name: "黑色电影侦探风漫画风格示例图" })).toBeInTheDocument();
    expect(resetComicRotation).not.toHaveBeenCalled();
    expect(fetchComicRotationStatus).toHaveBeenCalledTimes(1);
  });

  it("每日顺序显示明日模板；重置需要确认且取消不调用 API", async () => {
    vi.mocked(fetchComicRotationStatus).mockResolvedValue({ ...status, mode: "daily_sequential" });
    render(<Editor initial={{ ...saved, comic_template_mode: "daily_sequential" }} />);
    await screen.findByText("明日模板");
    fireEvent.click(screen.getByRole("button", { name: /重置进度/ }));
    fireEvent.click(await screen.findByRole("button", { name: /保\s*留/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /保\s*留/ })).not.toBeInTheDocument());
    expect(resetComicRotation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /重置进度/ }));
    const buttons = await screen.findAllByRole("button", { name: "重置进度" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(resetComicRotation).toHaveBeenCalledTimes(1));
  });
});
