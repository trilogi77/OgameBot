() => {
    const queue = [];
    document.querySelectorAll('#build_list li[data-technology], .construction [data-technology]').forEach(el => {
        const tid = el.getAttribute('data-technology');
        if (tid) {
            queue.push(parseInt(tid));
        }
    });
    return queue;
}
