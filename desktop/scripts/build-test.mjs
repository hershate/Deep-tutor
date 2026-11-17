#!/usr/bin/env node
// 构建脚本回归测试：P-154 的旧版本安装包清理。
//
// 仓库此前只有 Rust 测试通道与前端 Node 测试通道，构建脚本（desktop/scripts）
// 没有回归测试：desktop/scripts/tauri.mjs 原先只删除并重写与当前版本同名的
// 安装包，bundle 目录与仓库根 `dist/` 中的旧版本安装包长期并存，「dist/ 只保留
// 当前安装包及摘要」只由人工维护。本脚本用 Node 自带 test runner 覆盖清理
// 逻辑，不新增任何依赖：
// - 直接对临时目录调用 artifacts.mjs 的清理函数，断言旧版本安装包与摘要被删、
//   当前版本与无关文件保留；
// - 行为级断言发布流程：复制安装包 → 写摘要 → 清理 dist/ 与 bundle 两个目录，
//   以执行顺序（onStep 回调）固化「清理发生在复制与摘要写入之后」这一关键
//   不变量，不再依赖源码文本匹配（源码重排不会让测试失效）。
//
// 临时目录使用仓库 TEMP/ 子目录（不用系统 TEMP），测试结束即清理。
//
// 运行：node desktop/scripts/build-test.mjs（make test 与 CI 均执行）

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import {
  installerVersion,
  publishInstaller,
  removeStaleInstallers,
  resolveBundleDirectory,
  resolveDistDirectory,
} from './artifacts.mjs'
import { publishBuildArtifacts } from './tauri.mjs'

const productName = 'DeepTutor'
const currentVersion = '1.1.16'
const currentInstaller = `DeepTutor_${currentVersion}_x64-setup.exe`

/** 仓库根 TEMP 子目录——临时测试环境不落在系统 TEMP */
const repositoryTemp = fileURLToPath(new URL('../../TEMP', import.meta.url))

/** 建一个带多版本安装包的临时目录，测试结束删除 */
async function withArtifactDirectory(t, files) {
  await mkdir(repositoryTemp, { recursive: true })
  const directory = await mkdtemp(path.join(repositoryTemp, 'build-test-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  for (const [name, content] of Object.entries(files)) {
    await writeFile(path.join(directory, name), content)
  }
  return directory
}

/** 目录内的文件名集合 */
async function names(directory) {
  return (await readdir(directory)).sort()
}

test('P-154：安装包文件名解析出版本号，非本产品安装包返回 null', () => {
  assert.equal(installerVersion('DeepTutor_1.1.16_x64-setup.exe', productName), '1.1.16')
  assert.equal(installerVersion('DeepTutor_1.1.12_x64-setup.exe', productName), '1.1.12')
  assert.equal(installerVersion('DeepTutor_1.1.16_x64-setup.exe.sha256', productName), null)
  assert.equal(installerVersion('OtherApp_1.1.16_x64-setup.exe', productName), null)
  assert.equal(installerVersion('notes.txt', productName), null)
  assert.equal(installerVersion('DeepTutor_1.1.16_x64-setup.exe', ''), null)
})

test('P-154：旧版本安装包及其摘要被删除，当前版本与无关文件保留', async t => {
  const directory = await withArtifactDirectory(t, {
    'DeepTutor_1.1.16_x64-setup.exe': 'current',
    'DeepTutor_1.1.16_x64-setup.exe.sha256': 'current summary',
    'DeepTutor_1.1.12_x64-setup.exe': 'old',
    'DeepTutor_1.1.12_x64-setup.exe.sha256': 'old summary',
    'DeepTutor_1.1.15_x64-setup.exe': 'old',
    'DeepTutor_1.1.16_x64-setup.msi': 'other bundle format',
    'readme.txt': 'unrelated',
  })
  const removed = await removeStaleInstallers(directory, { productName, currentVersion })
  assert.deepEqual(removed.sort(), [
    'DeepTutor_1.1.12_x64-setup.exe',
    'DeepTutor_1.1.12_x64-setup.exe.sha256',
    'DeepTutor_1.1.15_x64-setup.exe',
  ])
  assert.deepEqual(await names(directory), [
    'DeepTutor_1.1.16_x64-setup.exe',
    'DeepTutor_1.1.16_x64-setup.exe.sha256',
    'DeepTutor_1.1.16_x64-setup.msi',
    'readme.txt',
  ])
  assert.equal(await readFile(path.join(directory, 'DeepTutor_1.1.16_x64-setup.exe'), 'utf8'), 'current')
})

test('P-154：只剩当前版本的目录清理后不再有待删项（幂等）', async t => {
  const directory = await withArtifactDirectory(t, {
    'DeepTutor_1.1.16_x64-setup.exe': 'current',
    'DeepTutor_1.1.16_x64-setup.exe.sha256': 'current summary',
  })
  assert.deepEqual(await removeStaleInstallers(directory, { productName, currentVersion }), [])
  assert.deepEqual(await names(directory), [
    'DeepTutor_1.1.16_x64-setup.exe',
    'DeepTutor_1.1.16_x64-setup.exe.sha256',
  ])
})

test('P-154：发布流程先复制并写摘要，再清理 dist/ 与 bundle 两个目录', async t => {
  const bundle = await withArtifactDirectory(t, {
    [currentInstaller]: 'current installer bytes',
    'DeepTutor_1.1.12_x64-setup.exe': 'old installer',
    'DeepTutor_1.1.12_x64-setup.exe.sha256': 'old summary',
  })
  const dist = await withArtifactDirectory(t, {
    'DeepTutor_1.1.15_x64-setup.exe': 'older installer',
    'DeepTutor_1.1.15_x64-setup.exe.sha256': 'older summary',
    'readme.txt': 'unrelated',
  })
  const steps = []
  const published = await publishInstaller({
    source: path.join(bundle, currentInstaller),
    destinationDirectory: dist,
    bundleDirectory: bundle,
    productName,
    currentVersion,
    onStep: step => steps.push(step),
  })
  // 顺序即不变量：清理必须发生在复制与摘要写入之后，且两个目录都要清理
  assert.deepEqual(steps, ['copy', 'sha256', 'cleanup:dist', 'cleanup:bundle'])
  assert.deepEqual(await names(dist), [
    currentInstaller,
    `${currentInstaller}.sha256`,
    'readme.txt',
  ])
  assert.deepEqual(await names(bundle), [currentInstaller])
  assert.equal(await readFile(path.join(dist, currentInstaller), 'utf8'), 'current installer bytes')
  const digest = createHash('sha256').update('current installer bytes').digest('hex')
  assert.equal(
    await readFile(path.join(dist, `${currentInstaller}.sha256`), 'utf8'),
    `${digest}  ${currentInstaller}\n`,
  )
  assert.equal(published.actual, digest)
  assert.deepEqual(published.removed.sort(), [
    'DeepTutor_1.1.12_x64-setup.exe',
    'DeepTutor_1.1.12_x64-setup.exe.sha256',
    'DeepTutor_1.1.15_x64-setup.exe',
    'DeepTutor_1.1.15_x64-setup.exe.sha256',
  ])
})

test('P-154：源安装包缺失时发布失败，不写摘要也不清理目录', async t => {
  const bundle = await withArtifactDirectory(t, { 'DeepTutor_1.1.12_x64-setup.exe': 'old' })
  const dist = await withArtifactDirectory(t, { 'DeepTutor_1.1.15_x64-setup.exe': 'older' })
  const steps = []
  await assert.rejects(
    publishInstaller({
      source: path.join(bundle, currentInstaller),
      destinationDirectory: dist,
      bundleDirectory: bundle,
      productName,
      currentVersion,
      onStep: step => steps.push(step),
    }),
    /ENOENT/,
  )
  // 复制未完成 → 摘要未写、清理未执行（dist/ 中的旧安装包仍在）
  assert.deepEqual(steps, [])
  assert.deepEqual(await names(dist), ['DeepTutor_1.1.15_x64-setup.exe'])
})

test('P-154：bundle 与 dist 目录按 --target/--debug 推导', () => {
  const desktop = path.join('C:', 'repo', 'desktop')
  assert.equal(
    resolveBundleDirectory(desktop, ['build']),
    path.join(desktop, 'src-tauri', 'target', 'release', 'bundle', 'nsis'),
  )
  assert.equal(
    resolveBundleDirectory(desktop, ['build', '--debug']),
    path.join(desktop, 'src-tauri', 'target', 'debug', 'bundle', 'nsis'),
  )
  assert.equal(
    resolveBundleDirectory(desktop, ['build', '--target', 'x86_64-pc-windows-msvc']),
    path.join(desktop, 'src-tauri', 'target', 'x86_64-pc-windows-msvc', 'release', 'bundle', 'nsis'),
  )
  assert.equal(
    resolveBundleDirectory(desktop, ['build', '--target=x86_64-pc-windows-msvc']),
    path.join(desktop, 'src-tauri', 'target', 'x86_64-pc-windows-msvc', 'release', 'bundle', 'nsis'),
  )
  assert.equal(
    resolveDistDirectory(path.join('C:', 'repo')),
    path.join('C:', 'repo', 'dist'),
  )
})

test('P-154：清理失败时错误信息显式标注「清理失败」，与打包失败区分', async t => {
  const bundle = await withArtifactDirectory(t, { [currentInstaller]: 'installer bytes' })
  const dist = await withArtifactDirectory(t, {})
  await assert.rejects(
    publishInstaller({
      source: path.join(bundle, currentInstaller),
      destinationDirectory: dist,
      bundleDirectory: path.join(bundle, 'missing-bundle-dir'),
      productName,
      currentVersion,
    }),
    /旧版本安装包清理失败/,
  )
  // 清理失败发生在复制与摘要之后：产物已在盘上，构建判失败但产物可用
  assert.deepEqual(await names(dist), [currentInstaller, `${currentInstaller}.sha256`])
})

test('P-154：构建成功后安装包发布到仓库根 dist/，两个目录的旧版本都被清理', async t => {
  // 用仓库真实 tauri.conf.json 的产品名与版本，避免测试与升版脱节
  const config = JSON.parse(
    await readFile(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'),
  )
  const installer = `${config.productName}_${config.version}_x64-setup.exe`
  const root = await withArtifactDirectory(t, {})
  const desktopRoot = path.join(root, 'desktop')
  const repositoryRoot = path.join(root, 'repo')
  const bundle = resolveBundleDirectory(desktopRoot, [])
  await mkdir(bundle, { recursive: true })
  await writeFile(path.join(bundle, installer), 'installer bytes')
  await writeFile(path.join(bundle, `${config.productName}_0.0.1_x64-setup.exe`), 'old installer')

  const published = await publishBuildArtifacts({
    desktop: desktopRoot,
    repository: repositoryRoot,
    config,
    args: [],
  })

  // dist/ 落在仓库根，只保留当前安装包与摘要
  assert.deepEqual(await names(path.join(repositoryRoot, 'dist')), [
    installer,
    `${installer}.sha256`,
  ])
  // NSIS bundle 目录中的旧版本安装包同样被清理，当前版本保留
  assert.deepEqual(await names(bundle), [installer])
  assert.deepEqual(published.removed, [`${config.productName}_0.0.1_x64-setup.exe`])
  const digest = createHash('sha256').update('installer bytes').digest('hex')
  assert.equal(published.actual, digest)
  assert.equal(
    await readFile(path.join(repositoryRoot, 'dist', `${installer}.sha256`), 'utf8'),
    `${digest}  ${installer}\n`,
  )
})
