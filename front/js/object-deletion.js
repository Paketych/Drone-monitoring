export function objectDeletionDialog(object, esc) {
  return `<p>Удалить объект <strong>${esc(object.address)}</strong>?</p>
    <p class="subtle" style="margin-top:12px">Будут удалены все облёты, фотографии, результаты проверок, расчёты стоимости и AI-сводки этого объекта. Отменить удаление нельзя.</p>
    <form id="object-delete-form" data-object-id="${esc(object.id)}" style="margin-top:20px">
      <div class="form-actions"><button type="button" class="btn" data-action="close-dialog">Отмена</button><button type="submit" class="btn danger">Удалить объект</button></div>
    </form>`;
}
