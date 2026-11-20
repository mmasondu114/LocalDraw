"""Per-panel candidate schedules that preserve every already saved image."""
import copy
from . import engine as e


def slots_for_panel(request, panel_id):
    return request.get('candidate_slots_by_panel', {}).get(
        panel_id, list(range(request['candidate_count'])))


def change_remaining_count(request, batches, count, mode='remaining'):
    e.need(request.get('action') == 'draw_imported', '只有逐格绘图任务支持调整后续数量')
    e.need(type(count) is int and 1 <= count <= 16, '后续每格数量须为 1–16 张')
    e.need(mode in ('remaining', 'total'), '数量模式无效')
    planned, retired, completed_panels, remaining_panels = {}, {}, 0, 0
    new_images = 0
    for key in request['targets']:
        old_slots = slots_for_panel(request, key)
        saved = [b for b in batches if b.get('render_options', {}).get('import_run') == request['run_key']
                 and b.get('panel_id') == key]
        completed = {s for b in saved if b.get('images') for s in b.get('candidate_indices', [])}
        # A slot already submitted unsuccessfully remains part of history too.
        consumed = {s for b in saved if b.get('images') or b.get('status') in ('done', 'failed', 'cancelled')
                    for s in b.get('candidate_indices', [])}
        consumed.update(request.get('retired_candidate_slots_by_panel', {}).get(key, []))
        retired[key] = sorted(consumed - completed)
        inflight = {s for b in saved if not b.get('images') and b.get('status') in ('running', 'uncertain')
                    for s in b.get('candidate_indices', [])}
        if set(old_slots) <= consumed and not inflight:
            planned[key] = list(old_slots)
            completed_panels += 1
            continue
        # A live worker may have paused immediately before submission. Keep that
        # reserved slot in the new allowance; never abandon or duplicate it.
        allowance = count if mode == 'remaining' else max(0, count - len(consumed))
        e.need(len(inflight) <= allowance, '当前格尚有待确认的图片，请确认完成状态后再调整')
        upcoming = sorted(inflight)
        next_slot = max(consumed | inflight, default=-1) + 1
        upcoming.extend(range(next_slot, next_slot + allowance - len(upcoming)))
        planned[key] = sorted(consumed | set(upcoming))
        remaining_panels += 1
        new_images += allowance
    e.need(remaining_panels > 0, '此任务的画格已全部生成，无需修改后续数量')
    result = copy.deepcopy(request)
    result.update(candidate_count=count, quantity_mode=mode, candidate_slots_by_panel=planned,
                  retired_candidate_slots_by_panel=retired,
                  quantity_revision=request.get('quantity_revision', 0) + 1,
                  quantity_change={'per_panel': count, 'remaining_panels': remaining_panels,
                                   'completed_panels': completed_panels,
                                   'mode': mode, 'new_images': new_images})
    return result
