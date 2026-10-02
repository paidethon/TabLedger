// End-to-end: login → upload synthetic bills → review → export → logout.
//
// Requires the harness (scripts/run_e2e_server.sh) which starts a fresh
// disposable instance and writes a random one-off password to the gitignored
// local file .e2e-password next to this spec.

import { expect, test } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, "..", "..", "tests", "fixtures", "synthetic");

const ADMIN = "admin";

// Fixed local path written by the harness; only guards a throwaway instance.
const passwordPath = path.join(here, ".e2e-password");
const PASSWORD = existsSync(passwordPath) ? readFileSync(passwordPath, "utf8").trim() : "";

test("full import-review-export journey", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);

  // -- login -------------------------------------------------------------
  await page.locator("input[autocomplete=username]").fill(ADMIN);
  await page.locator("input[autocomplete=current-password]").fill(PASSWORD);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).not.toHaveURL(/\/login/);

  // -- configure cards (fresh instance) -----------------------------------
  await page.goto("/settings?tab=accounts");
  await page.getByRole("button", { name: "添加银行卡" }).click();
  const rows = page.locator("main .grid.grid-cols-2");
  await rows.nth(0).locator("input").nth(0).fill("工商银行");
  await rows.nth(0).locator("input").nth(1).fill("0000");
  await rows.nth(0).locator("input").nth(2).fill("工行储蓄卡(0000)");
  await page.getByRole("textbox", { name: /本人姓名/ }).fill("张测试");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存")).toBeVisible();

  // -- import -------------------------------------------------------------
  await page.goto("/import");
  await page.locator("input[type=file]").setInputFiles([
    path.join(fixtures, "alipay.csv"),
    path.join(fixtures, "wechat.xlsx"),
    path.join(fixtures, "icbc.pdf"),
    path.join(fixtures, "boc.pdf"),
  ]);
  await page.getByRole("button", { name: /开始处理/ }).click();
  await expect(page).toHaveURL(/\/jobs\//);

  // -- wait for review -----------------------------------------------------
  await expect(page.getByText("待审核")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("对账说明")).toBeVisible();

  // -- classify unmatched keys ---------------------------------------------
  const expense: Record<string, [string, string]> = {
    测试超市: ["购物消费", "日常家居"],
    零钱提现: ["其他", "差额报销"],
    测试奶茶店: ["食品餐饮", "奶茶"],
    测试外卖平台: ["食品餐饮", "三餐"],
    测试电影: ["休闲娱乐", "电影"],
    张测试: ["送礼人情", "借出"],
    测试B商店: ["购物消费", "日常家居"],
    测试咖啡: ["食品餐饮", "饮料"],
    神秘未知商户XYZ: ["其他", "罚款赔偿"],
    测试视频网站: ["购物消费", "会员"],
    测试打车平台: ["出行交通", "打车"],
    测试A商店: ["购物消费", "日常家居"],
    测试文具店: ["购物消费", "办公用品"],
  };
  const income: Record<string, string> = { 测试科技有限公司: "工资" };
  for (let round = 0; round < 40; round += 1) {
    const row = page.locator("ul.divide-y > li", { hasText: "确定" }).first();
    if ((await row.count()) === 0) break;
    const text = await row.innerText();
    const merchant = text.split("\n")[0].trim();
    const isIncome = text.split("\n")[1].startsWith("收入");
    const selects = row.locator("select");
    if (isIncome) {
      await selects.nth(0).selectOption(income[merchant] ?? "其他");
    } else {
      const pair = expense[merchant];
      test.skip(!pair, `unexpected merchant ${merchant}`);
      await selects.nth(0).selectOption(pair![0]);
      await selects.nth(1).selectOption(pair![1]);
    }
    await row.getByRole("button", { name: "确定" }).click();
    await page.waitForTimeout(300);
  }
  await expect(page.getByText("分类完成")).toBeVisible({ timeout: 20_000 });

  // -- export & download ----------------------------------------------------
  await page.getByRole("button", { name: "生成并下载" }).click();
  await expect(page.getByText("已完成导出")).toBeVisible({ timeout: 30_000 });
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: /重新下载/ }).click();
  const file = await download;
  expect((await file.path()) ?? "").not.toBe("");

  // -- logout ---------------------------------------------------------------
  await page.getByRole("button", { name: "退出" }).click();
  await expect(page).toHaveURL(/\/login/);
});
