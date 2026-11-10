# -*- coding: utf-8 -*-
"""写入源ID

把每个图元的当前 ElementId 写入一个文本参数，供绑定链接后反查原号。

两种目标参数：
  1. 内置「注释」（推荐）：ALL_MODEL_INSTANCE_COMMENTS，所有图元天生自带，
     **不需要共享参数、不需要绑定**，最稳；
  2. 共享参数「源ID」：需要创建共享参数文件并绑定到类别，
     部分环境/模型上绑定会失效（本项目已复现），仅在确认可用时选。

注意：内置「注释」字段如果你们已经在用，请改用共享参数模式，避免覆盖。
"""

from __future__ import print_function

import codecs
import os
import traceback

from Autodesk.Revit.DB import (
    BuiltInParameter,
    BuiltInParameterGroup,
    CategoryType,
    ExternalDefinitionCreationOptions,
    FilteredElementCollector,
    ParameterType,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

SHARED_NAME = u"源ID"
GROUP_NAME = u"六吨工具箱"
COMMENT_BIP = BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS
MARK_BIP = BuiltInParameter.ALL_MODEL_MARK


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def model_categories():
    counts = {}
    for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
        category = safe_call(lambda e=elem: e.Category, None)
        if category is None:
            continue
        if safe_call(lambda c=category: c.CategoryType, None) != CategoryType.Model:
            continue
        key = category.Id.IntegerValue
        if key in counts:
            counts[key]["count"] += 1
        else:
            counts[key] = {"category": category, "count": 1}
    items = list(counts.values())
    items.sort(key=lambda x: -x["count"])
    return items


def iter_elements(category_ids):
    for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
        category = safe_call(lambda e=elem: e.Category, None)
        if category is None:
            continue
        if category_ids is not None and category.Id.IntegerValue not in category_ids:
            continue
        yield elem


def comment_param(elem):
    return safe_call(lambda: elem.get_Parameter(COMMENT_BIP), None)


def mark_param(elem):
    return safe_call(lambda: elem.get_Parameter(MARK_BIP), None)


def shared_param_file():
    app = doc.Application
    current = safe_call(lambda: app.SharedParametersFilename, u"")
    if current and os.path.isfile(current):
        return current
    target = os.path.join(os.path.expanduser(u"~"), u"Documents",
                          u"LDT_SharedParams.txt")
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    if (not os.path.isfile(target)) or os.path.getsize(target) == 0:
        with codecs.open(target, "w", "utf-8") as handle:
            handle.write(u"# LDT shared parameters\n")
    app.SharedParametersFilename = target
    return target


def ensure_definition(app, shared_file):
    group = safe_call(lambda: shared_file.Groups.get_Item(GROUP_NAME), None)
    if group is None:
        group = shared_file.Groups.Create(GROUP_NAME)
    definition = safe_call(lambda: group.Definitions.get_Item(SHARED_NAME), None)
    if definition is None:
        options = ExternalDefinitionCreationOptions(SHARED_NAME, ParameterType.Text)
        definition = group.Definitions.Create(options)
    return definition


def bind_param(definition, categories):
    app = doc.Application
    category_set = app.Create.NewCategorySet()
    inserted = 0
    for entry in categories:
        try:
            category_set.Insert(entry["category"])
            inserted += 1
        except Exception:
            continue
    binding = app.Create.NewInstanceBinding(category_set)
    errors = []
    for group in (BuiltInParameterGroup.PG_IDENTITY_DATA,
                  BuiltInParameterGroup.PG_DATA):
        try:
            doc.ParameterBindings.Insert(definition, binding, group)
            return u"新建绑定（类别 {0}）".format(inserted), True
        except Exception as ex:
            errors.append(u"Insert：{0}".format(ex))
        try:
            doc.ParameterBindings.ReInsert(definition, binding, group)
            return u"追加类别绑定（类别 {0}）".format(inserted), True
        except Exception as ex:
            errors.append(u"ReInsert：{0}".format(ex))
    return u"绑定失败：" + u" ｜ ".join(errors[-2:]), False


def binding_names():
    names = []
    try:
        iterator = doc.ParameterBindings.ForwardIterator()
        iterator.Reset()
        while iterator.MoveNext():
            definition = iterator.Key
            names.append(safe_call(lambda d=definition: d.Name, u"?"))
    except Exception:
        pass
    return names


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    categories = model_categories()
    if not categories:
        forms.alert(u"没有找到可绑定参数的模型类别。")
        return

    target = forms.SelectFromList.show(
        [u"内置「注释」参数（推荐，多数类别有）",
         u"内置「标记」参数（注释缺失时用）",
         u"共享参数「源ID」（需绑定，本机可能失效）"],
        title=u"写到哪个参数？", multiselect=False)
    if not target:
        return
    use_comment = target.startswith(u"内置「注释」")
    use_mark = target.startswith(u"内置「标记」")
    param_label = (u"「注释」" if use_comment
                   else (u"「标记」" if use_mark else u"「{0}」".format(SHARED_NAME)))
    if use_comment or use_mark:
        forms.alert(
            u"将把每个图元的 ID 写入{0}字段。\n"
            u"如果这个字段已经在用（放构件说明/编号），请改用共享参数模式。".format(param_label),
            title=u"注意")

    scope = forms.SelectFromList.show(
        [u"全模型（{0} 个类别）".format(len(categories)), u"按类别选择"],
        title=u"写入范围", multiselect=False)
    if not scope:
        return

    chosen = categories
    if scope.startswith(u"按类别"):
        labels = [u"{0}（{1}）".format(
            safe_call(lambda c=item["category"]: c.Name, u"?"), item["count"])
            for item in categories]
        picked = forms.SelectFromList.show(
            labels, title=u"选择类别（可多选）", multiselect=True, button_name=u"确定")
        if not picked:
            return
        chosen = [categories[labels.index(p)] for p in picked]

    policy = forms.SelectFromList.show(
        [u"只填空值（推荐）", u"全部覆盖"],
        title=u"写入策略", multiselect=False)
    if not policy:
        return
    overwrite = policy.startswith(u"全部覆盖")

    answer = forms.alert(
        u"将向 {0} 个类别写入 {1}，继续？".format(len(chosen), param_label),
        title=u"写入源ID", yes=True, no=True)
    if answer not in (True, u"Yes", u"yes", u"是"):
        return

    definition = None
    if not (use_comment or use_mark):
        try:
            path = shared_param_file()
            shared_file = doc.Application.OpenSharedParameterFile()
            if shared_file is None:
                forms.alert(u"共享参数文件打开失败：{0}".format(path))
                return
            with revit.Transaction(u"绑定源ID参数"):
                definition = ensure_definition(doc.Application, shared_file)
                message, ok = bind_param(definition, chosen)
                if ok:
                    doc.Regenerate()
            output.print_md(u"绑定：{0}".format(message))
            output.print_md(u"绑定表里的参数：{0}".format(
                u"、".join(binding_names()) or u"（空）"))
            if SHARED_NAME not in binding_names():
                forms.alert(
                    u"共享参数绑定没有生效（绑定表里没有「{0}」）。\n"
                    u"请重新运行并改选「内置注释」模式。".format(SHARED_NAME))
                return
        except Exception as ex:
            forms.alert(u"创建共享参数失败：{0}".format(ex))
            return

    category_ids = set(item["category"].Id.IntegerValue for item in chosen)
    written = 0
    skipped = 0
    missing = 0
    failed = []
    str_stats = {}
    try:
        with revit.Transaction(u"写入源ID"):
            for elem in iter_elements(category_ids):
                category_name = safe_call(lambda e=elem: e.Category.Name, u"?") or u"?"
                entry = str_stats.setdefault(category_name, {"w": 0, "s": 0, "m": 0})
                if use_comment:
                    param = comment_param(elem)
                elif use_mark:
                    param = mark_param(elem)
                else:
                    param = safe_call(lambda e=elem: e.LookupParameter(SHARED_NAME), None)
                    if param is None and definition is not None:
                        param = safe_call(lambda e=elem: e.get_Parameter(definition), None)
                if param is None:
                    missing += 1
                    entry["m"] += 1
                    continue
                if safe_call(lambda p=param: p.IsReadOnly, True):
                    skipped += 1
                    entry["s"] += 1
                    continue
                current = safe_call(lambda p=param: p.AsString(), u"") or u""
                if current and not overwrite:
                    skipped += 1
                    entry["s"] += 1
                    continue
                try:
                    param.Set(str(elem.Id.IntegerValue))
                    written += 1
                    entry["w"] += 1
                except Exception as ex:
                    failed.append(u"{0}：{1}".format(elem.Id.IntegerValue, ex))
    except Exception as ex:
        forms.alert(u"写入失败：{0}".format(ex))
        return

    output.print_md(u"## 写入源ID")
    output.print_md(u"目标参数：{0}".format(param_label))
    output.print_md(u"写入 **{0}** 个，跳过 **{1}** 个（已有值或只读），"
                    u"**{2}** 个图元没有该参数。".format(written, skipped, missing))

    rows = []
    for name in sorted(str_stats.keys(),
                       key=lambda n: -(str_stats[n]["m"] + str_stats[n]["s"])):
        entry = str_stats[name]
        if entry["m"] == 0 and entry["s"] == 0:
            continue
        rows.append([name, entry["w"], entry["s"], entry["m"]])
    if rows:
        output.print_table(
            rows[:30], title=u"按类别统计（只列有跳过或无参数的类别）",
            columns=[u"类别", u"写入", u"跳过", u"无参数"])
    if written == 0 and missing > 0:
        output.print_md(u"\n**没有写入任何值**：目标参数不存在或为只读，请改选另一种参数模式。")
    if failed:
        output.print_md(u"失败 {0} 个，前 5 条：\n".format(len(failed)) +
                        u"\n".join(u"- " + f for f in failed[:5]))
    output.print_md(u"\n绑定链接后，用「源ID 对照」就能把原号找回来。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"写入源ID")
