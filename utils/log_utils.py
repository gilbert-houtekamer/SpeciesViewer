# log_utils.py
def make_log_tail_accessor(log_box):
    def get_log_tail():
        return [ctrl.value for ctrl in log_box.controls if hasattr(ctrl, "value")]
    return get_log_tail
