"""Validate installed hand-detail nodes without submitting any generation job."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import comfyui, engine as e, hand_detail
from comic_workflow.paths import DATA


def main():
    cfg = comfyui.config()
    detail = hand_detail.config()
    e.need(hand_detail.capability()["ready"], "手部检测模型或运行环境未就绪")
    manager = comfyui.ComfyManager()
    print("检查本地绘图节点，不提交出图任务。", flush=True)
    try:
        manager.start()
        objects = comfyui.api("/object_info")
        graph = hand_detail.workflow(cfg["checkpoint"], cfg, detail, 42,
                                     "comic-hands/check/crop.png", "comic-hands/check/mask.png", [])
        for key, node in graph.items():
            name = node["class_type"]
            e.need(name in objects, "绘图节点未安装：" + name)
            inputs = objects[name]["input"]
            e.need(set(inputs.get("required", {})).issubset(node["inputs"]), "绘图节点缺少输入：" + name)
            for field, value in node["inputs"].items():
                spec = inputs.get("required", {}).get(field) or inputs.get("optional", {}).get(field)
                e.need(spec is not None, f"未知节点输入：{name}.{field}")
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    e.need(value[0] in graph, "节点连接缺失：" + key)
                    source = objects[graph[value[0]]["class_type"]]["output"]
                    e.need(value[1] < len(source) and source[value[1]] == spec[0],
                           f"节点连接类型不符：{name}.{field}")
                elif isinstance(spec[0], list) and name != "LoadImage":
                    e.need(value in spec[0], f"节点选项无效：{name}.{field}")
        report = {"status": "passed", "generation_submitted": False,
                  "checkpoint": cfg["checkpoint"], "node_count": len(graph),
                  "capability": hand_detail.capability(), "settings": detail}
        e.save(DATA / "test-results/hand-detail/setup-check.json", report)
        print("手部细化节点与连接检查通过；没有提交出图任务。", flush=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
