# Clash 公开路由模板

template.yaml 是供本地渲染器读取的路由骨架，包含业务组、规则、公开规则集及可用地理数据选项。它不是可直接使用的节点订阅。

## 来源

模板改编自 Aethersailor 的 [Custom OpenClash Rules](https://github.com/Aethersailor/Custom_OpenClash_Rules/blob/main/cfg/yaml/Custom_Clash.yaml)。
保留上游分流内容与规则集链接，去除上游节点、节点提供器、地区测速组及运行设置，并按稳定顺序重建业务组。
改编模板按上游 CC BY-SA 4.0 许可分发，见 LICENSE-template。引用的外部规则集继续遵守各自来源许可。

## 更新

工作流每天北京时间 03:17 调度，也支持手动运行。只在规则和策略组的引用验证、回归测试和 Mihomo 合成组检查都通过时更新模板。
公开仓库仅使用合成测试资料，不能提交本地订阅、节点凭据或成品配置。
