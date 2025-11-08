function openModal(src) {
  var modal = document.getElementById('modal');
  var modalImg = document.getElementById('modal-content');
  modal.style.display = 'flex';
  modalImg.src = src;
  // Удалено: modalImg.play() — это <img>, не <video>
}

function closeModal() {
  var modal = document.getElementById('modal');
  var modalImg = document.getElementById('modal-content');
  modal.style.display = 'none';
  modalImg.src = '';  // Сбрасываем src для остановки потока
  // Удалено: modalImg.pause() — не нужно для <img>
}

// Закрытие при клике на пустое пространство (но не на изображение)
document.getElementById('modal').addEventListener('click', function(event) {
  if (event.target === this) {
    closeModal();
  }
});