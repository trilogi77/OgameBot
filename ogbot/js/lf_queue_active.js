// True si la cola de Formas de vida está ocupada.
() => {
    return !!(document.querySelector('.lifeformItemWrapper .on') ||
              document.querySelector('.lf-buildlist .item') ||
              document.querySelector('#lf_build_list .item'));
}
