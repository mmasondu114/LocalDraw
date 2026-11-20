"""Refresh the acceptance ledger from saved checks without granting acceptance."""
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))


def save(path,data):
    (ROOT/path).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    path='data/lora-curation/current-acceptance.json'
    report=read(path)
    runtime_config=read('config/comfyui.json')
    native_default=runtime_config.get('regional_lora_backend')=='native_masked'
    coverage=read('data/lora-curation/download-status.json')
    names=read('data/test-results/lora-name-layout-final.json')
    report.update(complete=False,new_thread_permitted=False)
    report['lora_coverage'].update(verified=coverage['verified'],required=coverage['roster_total'],complete=False,
        missing_characters=coverage['missing_characters'],catalog_checksum_matches=259,
        documented_payload_integrity_exceptions=[{'name':'止颂','certificate':'data/lora-curation/research/lessing-adoption-certificate.json',
            'website_catalog_checksum_match':False,'trainer_payload_hash_match':True,'actual_cuda_identity_review':'passed','costume_review':'partial'}])
    report['naming_and_references'].update(weights=names['weights'],**names['games'],layout_violations=len(names['layout_violations']),
        missing_english_aliases=len(names['missing_english_aliases']),official_names_with_latin_characters=names['official_names_with_latin_characters'])
    extras=report['search'].setdefault('additional_reports',[])
    live='data/test-results/lora-search-live/lessing-current-library.json'
    if live not in extras:extras.append(live)
    report['search']['current_inventory']=names['weights']
    report['search']['current_inventory_live_report']=live
    full_log = ROOT/'data/test-results/current-full-tests.log'
    if full_log.exists():
        logged = full_log.read_text(encoding='utf-8')
        result = re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK\s*$', logged)
        if result:
            report['program_checks'] = {'passed':int(result[1]),'failed':0,
                'report':'data/test-results/current-full-tests.log','run_seconds':float(result[2]),
                'scope':'Latest recorded complete program suite; visual acceptance is tracked independently.'}
    multi=report['multi_character']
    multi['status']='partial_not_accepted'
    multi.update(production_backend=runtime_config.get('regional_lora_backend','native_lora_hooks'),
        native_masked_enabled_by_default=native_default)
    if report['anime_style']['baseline']!=runtime_config['checkpoint']:
        report['anime_style']['prior_baseline']=report['anime_style']['baseline']
        if 'single_character_review' in report['anime_style']:
            report['anime_style']['prior_single_character_review']=report['anime_style'].pop('single_character_review')
    report['anime_style'].update(baseline=runtime_config['checkpoint'],sampler=runtime_config['sampler_name'],
        cfg=runtime_config['cfg'],steps=runtime_config['steps'])
    if runtime_config['checkpoint']=='waiIllustriousSDXL_v170.safetensors':
        report['anime_style']['source']='https://civitai.com/models/827184?modelVersionId=2883731'
    if report.get('hires'):
        report['hires']['enabled_as_default']=bool(runtime_config.get('hires_fix'))
    if report.get('hand_detection'):
        report['hand_detection']['automatic_repair_enabled_as_default']=bool(runtime_config.get('hand_detail'))
    if not multi.get('current_trials'):
        multi.update(latest_trial='data/test-results/native-studio-batch/20261007-095103-898360',
        native_masked_studio_integration='src/comic_workflow/native_regional.py',
        native_masked_enabled_by_default=False,
        tested_native_counts=[2,3,4],tested_native_seeds=[71007,71008],
        native_studio_graph_report='data/test-results/attention-couple/20261007-094429-736680/visual-review.json',
        full_studio_batch_report='data/test-results/native-studio-batch/20261007-095103-898360/visual-review.json',
        observed_current_limits=['已查看的两人、三人、四人整幅遮罩试验人数正确，背景连续；角色服装饰品与部分眼睛仍有偏差。',
            '实际四人工作室批次成功加载五个LoRA、四张参考图，头脚完整；未通过全部参考图还原度验收。',
            '原纳米手部检测器误认袖口饰品且漏检手套；作者较大模型在此图找到两只真手，局部修复仍有合并手指。',
            '官方原神参考图仅显示上身，腿脚按明确提示词核对。',
            '默认后端尚未更换，新方案仍须完成默认模型与其他构图的实图验证。'])
    sources=multi.setdefault('primary_sources',[])
    for source in ['https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights',
                   'https://github.com/ramyma/A8R8_ComfyUI_nodes','https://github.com/cubiq/ComfyUI_IPAdapter_plus',
                   'https://huggingface.co/Bingsu/adetailer']:
        if source not in sources:sources.append(source)
    changes=multi['changes_verified']
    for change in ['按官方遮罩LoRA示例连接正负两路独立角色钩子，保持完整画幅而非局部潜空间裁剪。',
                   '逐区域只传入该角色的一人姿势图；固定失败种子恢复正确人数，并扩测2/3/4人。',
                   '官方立绘甘雨/莫娜候选与旧版相同参数GPU对照，服装更稳定；仍未据此宣布全部最佳版本。',
                   '可选原生整幅遮罩方案接入实际工作室分区/LoRA调用；四角色加一风格的上限前后端统一为5。',
                   '实际隔离工作室批次完成创建、角色分区、四参考图、五LoRA和GPU手部检测/修复；已查看原图和修复图。']:
        if change not in changes:changes.append(change)
    report['gap_search'].update(remaining_missing=10,
        newer_pack_report='data/lora-curation/research/newer-arknights-pack.json',
        newer_pack_missing_identity_training_tags=0,
        author_ccip_table='data/lora-curation/research/author-all-pack-ccip.csv',
        existing_pack_actual_gpu_report='data/test-results/gap-pack/20261007-090105-947091/visual-review.json')
    report['next_task']['status']='waiting_for_current_acceptance'
    report['studio']['lora_limit']=5
    save(path,report)
    cfg=read('config/multi-person-workflow.json')
    experiments=cfg.setdefault('experimental_components',[])
    experiments=[r for r in experiments if r.get('name') not in ('native_masked','A8R8_ComfyUI_nodes')]
    experiments.extend([
        {'name':'native_masked','source':'https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights',
         'enabled_by_default':native_default,'studio_integration':'src/comic_workflow/native_regional.py',
         'report':multi.get('current_trials',{}).get('production_four_people_partial',multi['native_studio_graph_report']),
         'acceptance':'count_and_continuity_passed_likeness_partial'},
        {'name':'A8R8_ComfyUI_nodes','repository':'https://github.com/ramyma/A8R8_ComfyUI_nodes',
         'revision':'8d617cea60f2cd9e55f3152be356141d19ad48a2','enabled_by_default':False,
         'local_vendor_changes':False,'report':'data/test-results/attention-couple/20261007-092546-413857/visual-review.json'}])
    cfg['experimental_components']=experiments
    save('config/multi-person-workflow.json',cfg)
    print(f"Acceptance remains incomplete: {coverage['verified']}/{coverage['roster_total']}, "
          f"{names['weights']} library files, {report['program_checks']['passed']} current checks passed; no new chat permitted.")


if __name__=='__main__':main()
